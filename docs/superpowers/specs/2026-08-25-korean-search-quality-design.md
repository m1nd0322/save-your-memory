# Korean-Aware Search Quality Design

## Outcome

Search must find Korean documents even when compound nouns are written without spaces (`온도보정` found by `온도`), return evidence instead of nothing when query terms never co-occur, and rank healthy, relevant files above broken ones. The `index`, `query`, `status`, and `lint` interfaces, the `SearchHit` shape, ASCII-safe JSON output, and the stdlib-only constraint remain unchanged.

## Problem

The current FTS5 index uses the default `unicode61` tokenizer, which splits only on whitespace and punctuation:

- Glued Korean compounds become single opaque tokens. `온도보정알고리즘` cannot be found by `온도`; particles produce unrelated tokens (`설정값`, `설정을`).
- The search ladder requires AND co-occurrence down to `minimum_terms = 2`. Queries whose core terms never appear in one file return empty even when strong single-term evidence exists.
- Default BM25 treats all columns equally and ignores extraction status, so `error`/`unsupported` records compete with fully indexed documents.

SQLite's built-in `trigram` tokenizer was evaluated and rejected: it cannot match two-character Korean words such as `온도`, which are common.

## Index architecture (schema version 4)

`catalog_chunks` gains an `ngrams` column holding the CJK bigram projection of `content`: every maximal run of CJK characters is expanded to its contiguous character bigrams, joined with spaces. Latin-script text is left to `unicode61` and produces no ngram text.

`catalog_search` gains a matching `ngrams` column. It stays an external-content FTS5 table over `catalog_chunks` with insert/update/delete triggers extended to carry `ngrams`. No second content shadow table is created, preserving the storage guarantees from the previous design.

Bigrams are derived by a pure helper (for example `cjk_ngrams(text) -> str`) so indexing, migration, and tests share one implementation. Ngram generation is O(content length) and roughly doubles the FTS footprint for CJK-heavy corpora; this is documented as expected.

## Query pipeline

Query tokenization and stopword filtering are unchanged. Each candidate term group keeps its existing strict AND ladder (same-chunk, then cross-chunk intersection), with one extension per CJK-containing term:

> A CJK term matches when the literal token appears in `relative_path`/`content` **or** every character bigram of the term appears in the `ngrams` index.

This makes `온도` match `온도보정` (bigram `온도` present) while requiring all bigrams of longer terms, keeping precision loss bounded. Order information inside multi-bigram terms is intentionally not enforced.

After the AND ladder exhausts, a final fallback runs the candidate tokens as a BM25-ranked OR union, deduplicates per source file, and returns those hits. An empty result now means "no term appears anywhere", not "terms did not co-occur".

## Ranking

- BM25 column weights favor filename/path and ngram evidence appropriately over generic body frequency; exact weights are fixed by ranking-behavior tests, not magic constants scattered in SQL.
- Files with `status` `unsupported` or `error` are demoted below contentful files at equal relevance rather than excluded, preserving path-only discoverability.

## Compatibility and migration

- Fresh databases create schema version 4 directly.
- Version 3 upgrades in place: rebuild `catalog_search` and its triggers with the `ngrams` column, backfill `catalog_chunks.ngrams` from stored chunk content through the shared helper, then set `user_version = 4`.
- Version 2 chains through the existing path-only backfill into the version 4 rebuild. Only adjacent versions upgrade; older schemas keep failing with the actionable rebuild message.
- Source files remain immutable; wiki pages, provenance, lint, retry-unreadable, and CLI JSON contracts are untouched.

## Verification

- Helper tests prove bigram extraction covers Hangul runs, mixed scripts, punctuation boundaries, and empty input.
- Store tests prove glued-compound recall, unchanged Latin behavior, OR-fallback results, status demotion ordering, absence of `catalog_search_content`, and trigger consistency after update/delete.
- Migration tests prove version 3 and version 2 catalogs upgrade with identical post-upgrade search results to a fresh rebuild.
- Full suite, compileall, and a temporary fixture `index`/`query` flow remain green.

## Future work

- Replace hand-rolled excerpts with FTS5 `snippet()` for tighter match positioning.
