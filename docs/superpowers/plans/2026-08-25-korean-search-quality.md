# Korean-Aware Search Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make wiki retrieval find Korean documents with glued compound nouns, fall back to ranked OR evidence instead of empty results, and rank broken files below healthy ones — schema version 4, no new runtime dependencies.

**Architecture:** A pure `cjk_ngrams` helper projects CJK text into character bigrams stored once in a new `catalog_chunks.ngrams` column and mirrored into the external-content FTS5 table by the existing triggers. Query terms containing CJK match via literal token OR complete-bigram presence; after the strict AND ladder, a BM25-ranked OR union supplies fallback evidence. Weighted BM25 and status demotion order results.

**Tech Stack:** Python 3.11+, standard-library `sqlite3`, SQLite FTS5, `unittest`

**Spec:** `docs/superpowers/specs/2026-08-25-korean-search-quality-design.md`

## Global Constraints

- Source files are immutable; generated data stays outside the repository.
- No new runtime dependency.
- Existing CLI commands, JSON contracts (`ensure_ascii=True`), and `SearchHit` fields remain compatible.
- Only adjacent schema versions upgrade; legacy schemas keep failing with the rebuild message.
- Every implementation behavior is introduced with a failing test first.

---

### Task 1: CJK bigram helper

**Files:**
- Modify: `save_your_memory/store.py`
- Test: `tests/test_store_and_wiki.py`

**Interfaces:**
- Produces: pure `cjk_ngrams(text) -> str` returning space-joined contiguous bigrams of each maximal CJK run.

- [x] **Step 1: Write failing helper tests**

```python
def test_cjk_ngrams_extracts_hangul_runs_and_skips_latin():
    self.assertEqual(cjk_ngrams("온도보정"), "온도 도보 보정")
    self.assertEqual(cjk_ngrams("온도 sensor"), "온도")
    self.assertEqual(cjk_ngrams("plain only"), "")
    self.assertEqual(cjk_ngrams(""), "")
```

Include mixed-script boundaries (`"ABC온도보정"`), punctuation between runs, and two-character words.

- [x] **Step 2: Run the targeted tests and confirm the helper is missing**

Run: `python -m unittest tests.test_store_and_wiki -v`

- [x] **Step 3: Implement the helper**

```python
_CJK_RUN = re.compile(
    r"[\u1100-\u11ff\u3130-\u318f\ua960-\ua97f\uac00-\ud7a3"
    r"\u2e80-\u9fff\uf900-\ufaff]+"
)

def cjk_ngrams(text: str) -> str:
    grams: list[str] = []
    for run in _CJK_RUN.findall(text):
        grams.extend(run[i : i + 2] for i in range(len(run) - 1))
    return " ".join(grams)
```

Hangul jamo and syllable ranges cover precomposed syllables; the CJK block covers Hanja. Single-character runs produce no bigrams by construction.

- [x] **Step 4: Re-run the targeted tests and confirm they pass**

Run: `python -m unittest tests.test_store_and_wiki -v`

### Task 2: Schema version 4 with ngram column

**Files:**
- Modify: `save_your_memory/store.py`
- Test: `tests/test_store_and_wiki.py`

**Interfaces:**
- Produces: `Catalog.SCHEMA_VERSION = 4`, `UPGRADABLE_SCHEMA_VERSIONS = frozenset({2, 3})`.
- Preserves: external-content FTS5 (no shadow table), trigger sync on insert/update/delete.

- [x] **Step 1: Write failing fresh-schema tests**

```python
columns = {row[1] for row in catalog.connection.execute("PRAGMA table_info(catalog_chunks)")}
self.assertIn("ngrams", columns)
search_columns = {row[2] for row in catalog.connection.execute("PRAGMA table_info(catalog_search)")}
self.assertIn("ngrams", search_columns)
assert catalog.connection.execute(
    "SELECT 1 FROM sqlite_master WHERE name='catalog_search_content'"
).fetchone() is None
```

Also prove an ingested Korean chunk stores non-empty ngrams and that update/delete keep the FTS index consistent through the triggers.

- [x] **Step 2: Run the targeted tests and confirm schema v3 fails them**

Run: `python -m unittest tests.test_store_and_wiki -v`

- [x] **Step 3: Add the column to `catalog_chunks`, extend `catalog_search` and all three triggers, and populate ngrams wherever chunks are written**

```sql
CREATE VIRTUAL TABLE catalog_search USING fts5(
    relative_path, content, ngrams,
    content='catalog_chunks', content_rowid='id',
    tokenize='unicode61'
);
```

Chunk insertion paths compute `ngrams` from content via the shared helper in the same transaction.

- [x] **Step 4: Re-run the targeted tests and confirm they pass**

Run: `python -m unittest tests.test_store_and_wiki -v`

### Task 3: In-place migration from versions 3 and 2

**Files:**
- Modify: `save_your_memory/store.py`
- Test: `tests/test_store_and_wiki.py`

**Interfaces:**
- Consumes: existing v3 catalogs (chunked FTS5) and v2 catalogs (path-only backfill chain).
- Produces: fully searchable version 4 catalog without re-extracting any source file.

- [x] **Step 1: Write failing migration tests that build real v3/v2 databases first**

Create a v3 catalog with the current code shape (or fixture builder), close it, reopen, and assert:

```python
version = catalog.connection.execute("PRAGMA user_version").fetchone()[0]
self.assertEqual(version, Catalog.SCHEMA_VERSION)
hits = catalog.search("온도보정 관련 문서 알려줘", limit=5)
self.assertTrue(hits)  # backfilled ngrams are searchable
```

Assert a v2 database upgrades through path-only backfill into v4 and that unsupported files remain path-searchable.

- [x] **Step 2: Run the targeted tests and confirm they fail against current upgrade logic**

Run: `python -m unittest tests.test_store_and_wiki -v`

- [x] **Step 3: Implement version detection and the v4 rebuild migration**

On open of an upgradable catalog: drop and recreate `catalog_search` plus triggers with the new column, add `catalog_chunks.ngrams`, backfill it from stored content in Python inside one transaction, then set `user_version = 4`. Non-adjacent versions keep raising the actionable rebuild error unchanged.

- [x] **Step 4: Re-run the targeted tests and confirm they pass, including legacy rejection**

Run: `python -m unittest tests.test_store_and_wiki -v`

### Task 4: CJK-aware query expansion

**Files:**
- Modify: `save_your_memory/store.py`
- Test: `tests/test_store_and_wiki.py`

**Interfaces:**
- Preserves: `_query_tokens` behavior, stopword filtering, AND ladder ordering, `SearchHit` fields.

- [x] **Step 1: Write failing recall tests**

```python
def test_glued_compound_is_found_by_prefix_word():
    ingest("notes/temperature.md", "본문에 온도보정알고리즘이 포함된 문서입니다.")
    hits = catalog.search("온도", limit=5)
    self.assertTrue(any(h.relative_path == "notes/temperature.md" for h in hits))
```

Cover: multi-term glued queries, Latin-only queries behaving exactly as before, and a term absent from both content and ngrams still yielding no false hit.

- [x] **Step 2: Run the targeted tests and confirm glued compounds are missed today**

Run: `python -m unittest tests.test_store_and_wiki -v`

- [x] **Step 3: Extend per-term matching with literal OR complete-bigram clauses during MATCH construction**

Each CJK-containing term contributes `"term" OR {ngrams}: ("bigram1" AND "bigram2" ...)` semantics while non-CJK terms stay literal.

- [x] **Step 4: Re-run the targeted tests and confirm they pass**

Run: `python -m unittest tests.test_store_and_wiki -v`

### Task 5: Ranked OR fallback when AND fails

**Files:**
- Modify: `save_your_memory/store.py`
- Test: `tests/test_store_and_wiki.py`

**Interfaces:**
- Produces: final fallback stage after the AND ladder exhausts.
- Preserves: one hit per file, existing result ordering guarantees for AND successes.

- [x] **Step 1: Write a failing fallback test**

```python
def test_terms_that_never_cooccur_still_return_ranked_evidence():
    ingest("a/alpha.md", "온도 센서 교체 절차만 있습니다.")
    ingest("b/beta.md", "펌프 캘리브레이션 기록만 있습니다.")
    hits = catalog.search("온도 펌프", limit=5)
    self.assertEqual({h.relative_path for h in hits}, {"a/alpha.md", "b/beta.md"})
```

Also assert exact AND matches still outrank OR-fallback files when both exist.

- [x] **Step 2: Run the targeted tests and confirm the query currently returns nothing**

Run: `python -m unittest tests.test_store_and_wiki -v`

- [x] **Step 3: Append the OR union stage with per-file deduplication and BM25 ranking**

- [x] **Step 4: Re-run the targeted tests and confirm they pass**

Run: `python -m unittest tests.test_store_and_wiki -v`

### Task 6: Weighted ranking and status demotion

**Files:**
- Modify: `save_your_memory/store.py`
- Test: `tests/test_store_and_wiki.py`

**Interfaces:**
- Produces: single source-of-truth weight constants used by every ranking query.
- Preserves: tie-breaking by relative path.

- [x] **Step 1: Write failing ordering tests**

A file whose filename contains the term outranks a body-only match at equal frequency; an `error` file ranks below a contentful file when both match equally.

```python
self.assertLess(paths.index("docs/온도센서.md"), paths.index("misc/note.md"))
self.assertLess(paths.index("reports/fresh.md"), paths.index("broken/scan.ppt"))
```

- [x] **Step 2: Run the targeted tests and confirm ordering is unweighted today**

Run: `python -m unittest tests.test_store_and_wiki -v`

- [x] **Step 3: Apply `bm25(...)` column weights and a status demotion term in every ranking ORDER BY/rank projection, including cross-chunk rank sums and the OR fallback**

- [x] **Step 4: Re-run the targeted tests and confirm they pass**

Run: `python -m unittest tests.test_store_and_wiki -v`

### Task 7: Documentation and full verification

**Files:**
- Modify: `README.md` (검색 성능 노트: CJK bigram 인덱스로 인한 예상 크기 증가)
- Modify: `AGENTS.md` if schema guidance changes materially
- Verify all modified files and generated temporary outputs.

**Interfaces:**
- Documentation explains expected index growth for CJK corpora and that old adjacent schemas auto-upgrade while older ones require manual rebuild.

- [x] Update README storage notes with the ngram size expectation and schema version.
- [x] Run `python -m unittest discover -s tests -v`.
- [x] Run `python -m compileall save_your_memory scripts tests`.
- [x] Run a temporary fixture `index`, `query`, `status`, and `lint` flow including one Korean glued-compound query.
- [x] Confirm machine-readable output stays ASCII-safe for a Korean query end to end.
- [x] Inspect the temporary SQLite schema: `ngrams` present, no `catalog_search_content`.
- [x] Scan tracked changes for credentials, private paths, and generated databases.
