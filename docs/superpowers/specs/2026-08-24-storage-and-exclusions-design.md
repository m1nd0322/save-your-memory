# Storage and Exclusions Design

## Outcome

`save-your-memory` must rebuild its generated catalog with a substantially smaller SQLite footprint while keeping the existing `index`, `query`, `status`, and `lint` interfaces. Generated data remains disposable and reproducible from the configured source root.

## Configuration

- Always exclude safety-critical generated/version-control directories: `.git`, `.hg`, `.svn`, `.omx`, `__pycache__`, and `node_modules`.
- By default also exclude common dependency/generated directories: `.venv`, `venv`, `env`, `site-packages`, `build`, `dist`, `downloads`, `vendor`, and `third_party`.
- By default exclude directory names matching `*.git`, which covers downloaded source trees such as `library.git`.
- By default exclude `.csv` files before they enter the scan result.
- Allow comma-separated overrides through `SAVE_YOUR_MEMORY_EXCLUDED_DIRECTORIES`, `SAVE_YOUR_MEMORY_EXCLUDED_DIRECTORY_GLOBS`, and `SAVE_YOUR_MEMORY_EXCLUDED_EXTENSIONS`. Safety-critical directory exclusions are always retained.
- Allow `SAVE_YOUR_MEMORY_CHUNK_BYTES` to set the target maximum search chunk size; default to 32,768 UTF-8 bytes and require a positive integer. Normal tokens remain intact even if a single token must exceed the target.

## Storage architecture

`catalog_entries` stores file and directory metadata only. Extracted text is split into non-overlapping, token-safe chunks and stored exactly once in `catalog_chunks`. Chunks preserve the source text exactly when concatenated in `chunk_index` order.

`catalog_search` is an FTS5 external-content table backed by `catalog_chunks`. It indexes `relative_path` and `content` but does not create a second FTS content shadow table. Insert, update, and delete triggers keep the FTS index synchronized with the chunk table. The relative path is included only on the first chunk so long files do not repeatedly index the same path.

Files without readable content receive one path-only chunk with an empty body. This keeps unsupported, too-large, and extraction-error files discoverable by filename without claiming that their contents were understood.

The schema uses integer rowids and foreign keys. Deleting or replacing an entry deletes its chunks first, which fires the external-content FTS delete trigger before metadata removal.

## Incremental data flow

1. Scan after applying directory-name, directory-glob, and file-extension exclusions.
2. Compare path, size, and modification timestamp with `catalog_entries`.
3. Extract only new, changed, or explicitly retried files.
4. Upsert metadata, delete previous chunks, split extracted content, and insert new chunks in one transaction.
5. Reconstruct full text from ordered chunks only when a Markdown page or full `CatalogRecord` is requested.
6. Search chunks with FTS5, rank matching chunks, retain the best chunk per source file, and return the existing `SearchHit` shape. If required terms fall on adjacent chunks, use a file-level intersection fallback.

## Compatibility and failure handling

- The cleared generated data directory creates the new schema directly.
- Schema version 2 upgrades to version 3 by backfilling path-only chunks for files that have no searchable body. Older contentful schemas fail with an actionable rebuild message instead of being deleted.
- Source files remain immutable.
- Excluded files and directories receive no catalog record or wiki page.
- Empty or unsupported files keep metadata plus one path-only chunk; their unreadable body is never represented as extracted content.
- Legacy `.ppt` files are copied to a temporary directory and converted to `.pptx` with an isolated LibreOffice profile or macro-disabled, read-only, hidden PowerPoint automation. The original file remains unchanged, and converter processes are supervised and terminated as a tree on timeout.

## Verification

- Configuration tests prove defaults, overrides, normalization, and validation.
- Scanner tests prove dependency/generated directories, `*.git` trees, and CSV files are excluded while ordinary documents remain.
- Store tests prove content reconstruction, multiple chunks, absence of an FTS content shadow table, trigger consistency after update/delete, one search hit per file, and legacy-schema rejection.
- Existing wiki, CLI, provenance, retry, security, and streaming tests remain green.
- Completion requires the full unit suite, compileall, a temporary end-to-end ingest/query/lint run, and a repository secret/path scan.
