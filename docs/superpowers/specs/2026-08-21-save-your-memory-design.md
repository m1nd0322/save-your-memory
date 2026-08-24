# Save Your Memory Design

## Outcome

`save-your-memory` is a local Codex plugin that turns the files below a Windows directory configured in `.env` into a persistent Markdown wiki. Later questions are answered by Codex from retrieved wiki evidence, with both wiki-page and original-file paths shown to the user.

## Product constraints

- `SAVE_YOUR_MEMORY_ROOT` is the required source root. It must resolve to an existing directory.
- Source files are immutable. The indexer never copies, moves, renames, edits, or deletes them.
- `SAVE_YOUR_MEMORY_HOME` is optional. It defaults to `%LOCALAPPDATA%\save-your-memory` on Windows.
- The generated layer contains `index.sqlite3`, `wiki/index.md`, `wiki/overview.md`, `wiki/log.md`, and stable pages under `wiki/sources/`.
- The core implementation uses Python 3.11+ and the standard library. Codex itself is the answer-generation LLM, so no model API key is required. Installed `pdftotext` or PyMuPDF may be used as an optional local PDF extractor.
- Every indexed file retains provenance: absolute source path, root-relative path, size, modification time, extraction status, and content hash when content is readable.

## Architecture

The Python package is split by responsibility. `config` loads `.env` and validates paths. `scanner` walks the source tree without following links or reparse points. `extractors` converts supported documents to text. `store` owns the incremental SQLite catalog and retrieval. `wiki` compiles catalog rows to Markdown and maintains the index and append-only log. `cli` exposes `index`, `query`, `status`, and `lint` commands.

The Codex plugin is skills-only. Its `SKILL.md` runs the local CLI to retrieve the smallest relevant evidence set, then asks Codex to answer from that evidence. This follows the official minimal plugin shape: `.codex-plugin/plugin.json` plus at least one skill.

## Data flow

1. Load the selected `.env` file and process environment, with process values taking precedence.
2. Validate that the source root exists and that the output home is not indexed as source content.
3. Recursively enumerate directories and files. Do not follow links, junctions, or reparse points.
4. Compare each file's relative path, size, and nanosecond modification time with SQLite. Re-extract only new or changed files; remove only compiled records/pages for deleted sources.
5. Extract readable content. Plain text uses deterministic encoding fallbacks; OOXML uses its ZIP/XML payload. Unknown binary formats remain discoverable with an explicit `unsupported` status.
6. Write stable Markdown source pages and regenerate the navigation index and overview. Append an ingest summary to `wiki/log.md`.
7. Query SQLite FTS, returning ranked excerpts plus source and wiki paths. Codex synthesizes the final answer and cites those paths.

## Error handling and safety

- A missing or invalid root is a configuration error with a non-zero exit code.
- Permission, decode, archive, or XML failures are recorded per file and do not abort the entire scan.
- Files larger than `SAVE_YOUR_MEMORY_MAX_FILE_BYTES` are indexed as metadata with `too_large` extraction status.
- The output directory, `.git`, `.omx`, and common cache directories are excluded when they occur below the source root.
- Generated page deletion is limited to pages previously recorded by the catalog.

## Supported content

The first release extracts common text files and `.docx`, `.pptx`, and `.xlsx` files without third-party packages. Unknown extensions are content-sniffed so readable source/configuration files are not lost merely because their suffix is uncommon. Every remaining binary file is still indexed by path and metadata. PDF text extraction uses `pdftotext` when available and otherwise falls back to an installed PyMuPDF package; if neither exists, the record reports the unavailable extractor.

## Verification

Unit tests cover configuration precedence and validation, recursive traversal boundaries, text/OOXML extraction, incremental update/removal, Markdown provenance, retrieval, and CLI JSON output. Completion additionally requires plugin-manifest validation, a full unittest run, Python bytecode compilation, and an end-to-end smoke run over a temporary Windows fixture tree.
