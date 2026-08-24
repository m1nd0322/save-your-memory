---
name: save-your-memory
description: Index and query a persistent local Markdown wiki built from the Windows directory configured by SAVE_YOUR_MEMORY_ROOT. Use when the user asks to remember, index, refresh, locate, search, or answer questions from files on their hard drive, or asks where stored information came from.
---

# Save Your Memory

Use the bundled Python CLI as the retrieval layer and Codex as the answer-generation layer. Do not require an external LLM API key.

## Locate the CLI and configuration

1. Treat the directory two levels above this `SKILL.md` as `PLUGIN_ROOT`.
2. Use `python "<PLUGIN_ROOT>/scripts/save_your_memory.py"` with an absolute script path.
3. Select the environment file in this order:
   - a path explicitly supplied by the user;
   - `SAVE_YOUR_MEMORY_ENV_FILE` from the process environment;
   - `.env` in the current working directory;
   - `.env` in `PLUGIN_ROOT`.
4. If none exists, explain that `SAVE_YOUR_MEMORY_ROOT` must point to the Windows parent directory and show the `.env.example` format.

Never display the full environment file. Only report the resolved source root and wiki home returned by the CLI.

## Index or refresh

Run:

```powershell
python "<PLUGIN_ROOT>/scripts/save_your_memory.py" --env-file "<ENV_FILE>" index --json
```

Report added, updated, unchanged, removed, extraction-error, and scan-error counts. Make it explicit that unsupported binary files remain indexed by path and metadata.

When the user asks to retry unreadable files after extractor support changes, run:

```powershell
python "<PLUGIN_ROOT>/scripts/save_your_memory.py" --env-file "<ENV_FILE>" index --retry-unreadable --json
```

This retries only records previously marked `unsupported` or `error`; unchanged binary results are not rewritten.

## Answer a question

1. Run the query using the user's wording, not an invented keyword summary:

```powershell
python "<PLUGIN_ROOT>/scripts/save_your_memory.py" --env-file "<ENV_FILE>" query "<QUESTION>" --limit 5 --json
```

2. Read the smallest relevant set of returned `wiki_path` pages, normally the top 1–3.
3. Answer only claims supported by those pages. Preserve uncertainty and extraction errors.
4. End with a short `Sources` list containing clickable absolute paths for both the generated wiki pages and original `source_path` files.
5. If retrieval returns no evidence, say so. Run one index refresh and retry once when the catalog may be empty or stale; do not fabricate an answer.
6. Reply in the user's language unless they request another language.

## Status and consistency

Run status:

```powershell
python "<PLUGIN_ROOT>/scripts/save_your_memory.py" --env-file "<ENV_FILE>" status --json
```

Run wiki lint:

```powershell
python "<PLUGIN_ROOT>/scripts/save_your_memory.py" --env-file "<ENV_FILE>" lint --json
```

When lint fails, distinguish missing managed pages from orphan pages. Do not delete orphan pages without an explicit user request.

## Safety rules

- Never edit, move, rename, or delete a source file under `SAVE_YOUR_MEMORY_ROOT`.
- Never traverse symlinks, junctions, or Windows reparse points.
- Do not use raw source files as the first query surface. Search the compiled wiki, then follow its provenance.
- Do not claim a file's unreadable binary payload was understood; report its extraction status.
