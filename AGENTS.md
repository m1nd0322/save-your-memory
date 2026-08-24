# AGENTS.md

Local-first knowledge plugin: compiles a Windows file tree into a Markdown wiki + SQLite FTS5 catalog. Stdlib-only Python 3.11+ (`dependencies = []` is intentional).

## Commands

```sh
python -m unittest discover -s tests            # full suite (stdlib unittest, no pytest)
python -m unittest tests.test_store_and_wiki.CatalogAndWikiTests.test_name   # single test
python -m compileall save_your_memory scripts tests                          # syntax check
python scripts/save_your_memory.py --env-file .env index --json               # run indexer
```

CLI subcommands: `index [--retry-unreadable]`, `query "<q>" --limit N`, `status`, `lint` — always pass `--json` for machine-readable output.

## Platform reality

The target platform is Windows 10/11. On macOS, expect a few pre-existing test failures that are environmental, not regressions: OOXML extractor tests error if Homebrew Python lacks `expat`, and symlink-path tests fail because macOS resolves `/tmp` to `/private/tmp`. Confirm a failure reproduces on Windows semantics before "fixing" it.

Optional extractors: PDF via `pdftotext` or PyMuPDF; legacy `.ppt` via LibreOffice `soffice` or PowerPoint COM. Missing converters degrade to filename/path-only search by design — don't make them hard-fail.

## Hard invariants

- Files under `SAVE_YOUR_MEMORY_ROOT` are **immutable**: never edit, move, rename, or delete source files. Generated data lives only in `SAVE_YOUR_MEMORY_HOME` (must differ from root — enforced).
- Never traverse symlinks/junctions/reparse points anywhere in scanning or sync.
- No new runtime dependencies. Everything is Python stdlib + SQLite FTS5.
- JSON CLI output uses `ensure_ascii=True` on purpose (Windows locale encodings corrupt non-ASCII stdout). Don't change it.
- Never commit `.env`, real user paths, or personal documents; add reusable data as fixtures under `tests/fixtures/`.

## Sync points when changing things

- **Catalog schema**: `Catalog.SCHEMA_VERSION` in `store.py`. Legacy DBs are never auto-deleted or migrated across major versions (v1 requires manual rebuild; only adjacent versions in `UPGRADABLE_SCHEMA_VERSIONS` auto-upgrade with backfill). Any schema change needs a matching upgrade/backfill test.
- **Plugin manifests must stay consistent**: root `plugin.json` (Copilot Agent Plugins 1.0), `.codex-plugin/plugin.json` (Codex), `skills/save-your-memory/SKILL.md`, and `pyproject.toml` all carry the version/name; `tests/test_copilot_plugin.py` enforces manifest↔skill consistency.
- **Env precedence**: process environment overrides `.env` values (`config.py`). Relative env paths resolve against the env-file's directory.

## Conventions

- Docs/README are written in Korean; code identifiers and comments in English.
- Design work lives in `docs/superpowers/specs/YYYY-MM-DD-*.md` with implementation plans in `docs/superpowers/plans/`; plans are TDD (each behavior lands with a failing test first) using checkbox task tracking.
- Commit messages: short English imperatives (e.g., "Optimize storage with chunked FTS5").
