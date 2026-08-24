# Storage and Exclusions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the generated catalog with configurable noise exclusions and a chunked external-content FTS5 index that stores extracted text once in SQLite.

**Architecture:** `Settings` owns normalized exclusion and chunk-size policy, `scanner` removes excluded paths before extraction, and `Catalog` stores metadata separately from ordered content chunks. SQLite triggers synchronize the external-content FTS5 index while `Catalog` preserves the existing record and search interfaces.

**Tech Stack:** Python 3.11+, standard-library `sqlite3`, SQLite FTS5, `unittest`

**Spec:** `docs/superpowers/specs/2026-08-24-storage-and-exclusions-design.md`

## Global Constraints

- Source files are immutable.
- Generated data is reproducible and remains outside the repository.
- No new runtime dependency.
- Existing CLI and plugin invocation formats remain compatible.
- Every implementation behavior is introduced with a failing test first.

---

### Task 1: Configurable scan exclusions

**Files:**
- Modify: `save_your_memory/config.py`
- Modify: `save_your_memory/scanner.py`
- Test: `tests/test_config_and_scanner.py`

**Interfaces:**
- Produces: `Settings.excluded_directories`, `Settings.excluded_directory_globs`, `Settings.excluded_extensions`, and `Settings.chunk_bytes`.
- Consumes: comma-separated `.env` values with process-environment precedence.

- [ ] **Step 1: Write failing configuration and scanner tests**

```python
def test_default_noise_directories_and_csv_files_are_excluded():
    settings = Settings.load(env={"SAVE_YOUR_MEMORY_ROOT": str(root)}, env_file=None)
    paths = {entry.relative_path.as_posix() for entry in scan_tree(settings).entries}
    assert "keep.md" in paths
    assert "data.csv" not in paths
    assert "venv" not in paths
    assert "library.git" not in paths
```

- [ ] **Step 2: Run the targeted tests and confirm missing settings/scanner behavior fails**

Run: `python -m unittest tests.test_config_and_scanner -v`

- [ ] **Step 3: Implement normalized list parsing, positive chunk-size validation, and scanner matching**

```python
excluded_extensions = tuple(
    item if item.startswith(".") else f".{item}"
    for item in _parse_list(raw_extensions)
)
```

- [ ] **Step 4: Re-run the targeted tests and confirm they pass**

Run: `python -m unittest tests.test_config_and_scanner -v`

### Task 2: Chunked external-content FTS5 catalog

**Files:**
- Modify: `save_your_memory/store.py`
- Modify: `save_your_memory/wiki.py`
- Test: `tests/test_store_and_wiki.py`

**Interfaces:**
- Produces: `split_content_chunks(content, chunk_chars) -> tuple[str, ...]`.
- Preserves: `Catalog.sync`, `Catalog.get`, `Catalog.iter_records`, `Catalog.search`, `CatalogRecord`, and `SearchHit` public behavior.

- [ ] **Step 1: Write failing schema, chunk reconstruction, search deduplication, update/delete, and legacy-schema tests**

```python
columns = {row[1] for row in catalog.connection.execute("PRAGMA table_info(catalog_entries)")}
assert "content" not in columns
assert catalog.connection.execute(
    "SELECT 1 FROM sqlite_master WHERE name='catalog_search_content'"
).fetchone() is None
assert catalog.get("note.txt").content == original_content
```

- [ ] **Step 2: Run the targeted tests and confirm the old single-row/contentful schema fails**

Run: `python -m unittest tests.test_store_and_wiki -v`

- [ ] **Step 3: Implement metadata/chunk tables, external-content FTS5, and synchronization triggers**

```sql
CREATE TABLE catalog_chunks (
    id INTEGER PRIMARY KEY,
    entry_id INTEGER NOT NULL REFERENCES catalog_entries(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    relative_path TEXT NOT NULL,
    content TEXT NOT NULL,
    UNIQUE(entry_id, chunk_index)
);
CREATE VIRTUAL TABLE catalog_search USING fts5(
    relative_path,
    content,
    content='catalog_chunks',
    content_rowid='id',
    tokenize='unicode61'
);
```

- [ ] **Step 4: Reconstruct content lazily and update wiki compilation to fetch full text only for changed pages**

```python
record = self.catalog.get(metadata.relative_path) if needs_rebuild else metadata
```

- [ ] **Step 5: Rank chunks, select one best chunk per entry, and preserve provenance output**

```sql
row_number() OVER (
    PARTITION BY chunks.entry_id
    ORDER BY catalog_search.rank, catalog_search.rowid
) AS hit_order
```

- [ ] **Step 6: Re-run store/wiki tests and confirm they pass**

Run: `python -m unittest tests.test_store_and_wiki -v`

### Task 3: CLI wiring and documentation

**Files:**
- Modify: `save_your_memory/cli.py`
- Modify: `.env.example`
- Modify: `README.md`
- Test: `tests/test_cli.py`

**Interfaces:**
- `index` passes `settings.chunk_chars` to catalog synchronization.
- Documentation names all new settings and explains that schema changes require a clean rebuild.

- [ ] **Step 1: Write a failing CLI test with a small configured chunk size and exclusions**
- [ ] **Step 2: Run `python -m unittest tests.test_cli -v` and confirm failure**
- [ ] **Step 3: Wire the setting through the CLI and document defaults/overrides/rebuild behavior**
- [ ] **Step 4: Re-run CLI tests and confirm they pass**

### Task 4: Full verification

**Files:**
- Verify all modified files and generated temporary outputs.

**Interfaces:**
- Consumes all public commands and produces fresh completion evidence.

- [ ] Run `python -m unittest discover -s tests -v`.
- [ ] Run `python -m compileall save_your_memory scripts tests`.
- [ ] Run a temporary fixture `index`, `query`, `status`, and `lint` flow.
- [ ] Inspect the temporary SQLite schema and verify no `catalog_search_content` table exists.
- [ ] Scan tracked changes for credentials, private paths, and generated databases.
- [ ] Review `git diff --check` and `git status --short`.
