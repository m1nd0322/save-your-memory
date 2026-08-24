# Save Your Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local Codex plugin that incrementally compiles an env-configured Windows file tree into a persistent, source-linked Markdown wiki and retrieves evidence for LLM answers.

**Architecture:** A standard-library Python package separates configuration, traversal, extraction, catalog/retrieval, wiki compilation, and CLI orchestration. A skills-only Codex plugin invokes the CLI and uses Codex as the synthesis layer.

**Tech Stack:** Python 3.11+, `sqlite3`, `unittest`, Codex skills-only plugin manifest

**Spec:** `docs/superpowers/specs/2026-08-21-save-your-memory-design.md`

## Global Constraints

- Source files are immutable.
- `SAVE_YOUR_MEMORY_ROOT` is required and points to an existing directory.
- No third-party runtime dependency or external LLM API key.
- Every answerable result includes both source and generated-wiki provenance.
- Links, junctions, and reparse points are never traversed.

---

### Task 1: Configuration and safe recursive discovery

**Files:**
- Create: `save_your_memory/config.py`
- Create: `save_your_memory/scanner.py`
- Test: `tests/test_config_and_scanner.py`

**Interfaces:**
- Produces: `Settings.load(...) -> Settings`, `scan_tree(settings) -> ScanResult`

- [ ] Write tests for env precedence, invalid roots, nested discovery, exclusions, and link safety.
- [ ] Run the tests and confirm they fail because the package does not exist.
- [ ] Implement configuration and traversal with `pathlib` and `os.scandir`.
- [ ] Run the targeted tests and confirm they pass.

### Task 2: Content extraction

**Files:**
- Create: `save_your_memory/extractors.py`
- Test: `tests/test_extractors.py`

**Interfaces:**
- Consumes: discovered file paths and maximum-byte limit.
- Produces: `extract_content(path, max_bytes) -> ExtractionResult`

- [ ] Write tests for UTF-8/CP949 text, unsupported binary, size limits, and hand-built DOCX/PPTX/XLSX fixtures.
- [ ] Run the tests and confirm the missing extractor failure.
- [ ] Implement deterministic text and ZIP/XML extraction plus optional `pdftotext` use.
- [ ] Run the targeted tests and confirm they pass.

### Task 3: Incremental catalog, wiki compiler, and retrieval

**Files:**
- Create: `save_your_memory/store.py`
- Create: `save_your_memory/wiki.py`
- Test: `tests/test_store_and_wiki.py`

**Interfaces:**
- Produces: `Catalog.sync(...)`, `Catalog.search(...)`, `WikiCompiler.compile(...)`, `WikiCompiler.lint(...)`.

- [ ] Write tests for first ingest, unchanged reuse, changed update, deleted-source cleanup, stable pages, provenance, index/log updates, and ranked search.
- [ ] Run the tests and confirm the missing implementation failure.
- [ ] Implement SQLite schema/FTS and Markdown compilation.
- [ ] Run the targeted tests and confirm they pass.

### Task 4: CLI and Codex plugin packaging

**Files:**
- Create: `save_your_memory/cli.py`
- Create: `save_your_memory/__main__.py`
- Create: `scripts/save_your_memory.py`
- Create: `tests/test_cli.py`
- Create: `.codex-plugin/plugin.json`
- Create: `skills/save-your-memory/SKILL.md`
- Create: `.env.example`
- Create: `README.md`
- Create: `pyproject.toml`

**Interfaces:**
- Produces: `python -m save_your_memory index|query|status|lint` and the `save-your-memory` plugin workflow.

- [ ] Write subprocess tests for `index`, JSON `query`, `status`, and invalid configuration.
- [ ] Run the tests and confirm the missing CLI failure.
- [ ] Implement the CLI and thin script entry point.
- [ ] Scaffold and fill the plugin manifest and skill instructions.
- [ ] Run CLI tests and plugin validation.

### Task 5: End-to-end verification and installation handoff

**Files:**
- Modify: `README.md`
- Create: `.gitignore`

**Interfaces:**
- Consumes: all preceding public commands.
- Produces: verified local plugin source and reproducible setup instructions.

- [ ] Run the full unit test suite.
- [ ] Run `python -m compileall` on package, scripts, and tests.
- [ ] Run an end-to-end temporary-tree ingest/query/lint smoke test.
- [ ] Validate `.codex-plugin/plugin.json` with the plugin-creator validator.
- [ ] Audit every design requirement against fresh command/file evidence.

