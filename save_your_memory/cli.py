from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from .config import ConfigurationError, Settings
from .scanner import scan_tree
from .store import Catalog, CatalogError, SyncSummary
from .wiki import WikiCompiler


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="save-your-memory",
        description="Compile a Windows file tree into a persistent Markdown wiki.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Path to .env (default: .env in the current directory when present)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser("index", help="Scan and compile the wiki")
    index_parser.add_argument("--json", action="store_true", dest="as_json")
    index_parser.add_argument(
        "--retry-unreadable",
        action="store_true",
        help="Retry files previously marked unsupported or error",
    )

    query_parser = subparsers.add_parser("query", help="Retrieve wiki evidence")
    query_parser.add_argument("question")
    query_parser.add_argument("--limit", type=int, default=5)
    query_parser.add_argument("--json", action="store_true", dest="as_json")

    status_parser = subparsers.add_parser("status", help="Show catalog status")
    status_parser.add_argument("--json", action="store_true", dest="as_json")

    lint_parser = subparsers.add_parser("lint", help="Check wiki consistency")
    lint_parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def _settings(env_file: Path | None) -> Settings:
    selected = env_file
    if selected is None:
        default = Path.cwd() / ".env"
        selected = default if default.is_file() else None
    return Settings.load(env_file=selected)


def _emit(payload: dict[str, object], as_json: bool) -> None:
    if as_json:
        # JSON is ASCII-safe so Windows locale encodings cannot corrupt the
        # machine-readable stream. json.loads restores the original Unicode.
        print(json.dumps(payload, ensure_ascii=True, indent=2))
        return
    for key, value in payload.items():
        print(f"{key}: {value}")


def _sync_payload(sync: SyncSummary) -> dict[str, object]:
    payload = asdict(sync)
    changed_paths = payload.pop("changed_paths")
    removed_wiki_paths = payload.pop("removed_wiki_paths")
    payload.update(
        {
            "changed_path_count": len(changed_paths),
            "changed_path_sample": changed_paths[:20],
            "removed_wiki_path_count": len(removed_wiki_paths),
            "removed_wiki_path_sample": removed_wiki_paths[:20],
        }
    )
    return payload


def _run_index(settings: Settings, as_json: bool, retry_unreadable: bool) -> int:
    scan = scan_tree(settings)
    with Catalog(settings.home / "index.sqlite3") as catalog:
        retry_statuses = (
            frozenset({"unsupported", "error"})
            if retry_unreadable
            else frozenset()
        )
        sync = catalog.sync(
            scan,
            settings.max_file_bytes,
            retry_statuses=retry_statuses,
        )
        compiled = WikiCompiler(settings, catalog).compile(sync)
    payload: dict[str, object] = {
        "command": "index",
        "root": str(settings.root),
        "home": str(settings.home),
        "retry_unreadable": retry_unreadable,
        "sync": _sync_payload(sync),
        "compiled": asdict(compiled),
        "wiki_index": compiled.index_path,
    }
    _emit(payload, as_json)
    return 0


def _run_query(settings: Settings, question: str, limit: int, as_json: bool) -> int:
    if limit <= 0:
        raise ConfigurationError("--limit must be greater than zero")
    with Catalog(settings.home / "index.sqlite3") as catalog:
        hits = catalog.search(question, limit=limit)
        compiler = WikiCompiler(settings, catalog)
        compiler.append_query(question, len(hits))
    results = [
        {
            "relative_path": hit.relative_path,
            "source_path": hit.absolute_path,
            "wiki_path": str((settings.home / hit.wiki_path).resolve(strict=False)),
            "status": hit.status,
            "excerpt": hit.excerpt,
            "score": hit.score,
        }
        for hit in hits
    ]
    payload: dict[str, object] = {
        "command": "query",
        "question": question,
        "results": results,
    }
    _emit(payload, as_json)
    return 0


def _run_status(settings: Settings, as_json: bool) -> int:
    with Catalog(settings.home / "index.sqlite3") as catalog:
        records = catalog.iter_records(include_content=False)
        file_count = 0
        directory_count = 0
        extraction_status: Counter[str] = Counter()
        for record in records:
            if record.kind == "file":
                file_count += 1
                extraction_status[record.status] += 1
            elif record.kind == "directory":
                directory_count += 1
        search_backend = catalog.search_backend
    payload: dict[str, object] = {
        "command": "status",
        "root": str(settings.root),
        "home": str(settings.home),
        "files": file_count,
        "directories": directory_count,
        "extraction_status": dict(extraction_status),
        "search_backend": search_backend,
        "wiki_index": str(settings.home / "wiki/index.md"),
    }
    _emit(payload, as_json)
    return 0


def _run_lint(settings: Settings, as_json: bool) -> int:
    with Catalog(settings.home / "index.sqlite3") as catalog:
        report = WikiCompiler(settings, catalog).lint()
    payload: dict[str, object] = {
        "command": "lint",
        "issues": list(report.issues),
        "ok": report.ok,
    }
    _emit(payload, as_json)
    return 0 if report.ok else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        settings = _settings(args.env_file)
        if args.command == "index":
            return _run_index(settings, args.as_json, args.retry_unreadable)
        if args.command == "query":
            return _run_query(settings, args.question, args.limit, args.as_json)
        if args.command == "status":
            return _run_status(settings, args.as_json)
        if args.command == "lint":
            return _run_lint(settings, args.as_json)
        parser.error(f"Unknown command: {args.command}")
    except (ConfigurationError, CatalogError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    return 2
