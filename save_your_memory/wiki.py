from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .config import Settings
from .paths import stable_wiki_path
from .store import Catalog, CatalogRecord, SyncSummary


@dataclass(frozen=True)
class CompileSummary:
    source_pages: int
    removed_pages: int
    index_path: str
    log_path: str


@dataclass(frozen=True)
class LintReport:
    ok: bool
    issues: tuple[str, ...]


def _yaml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _content_fence(content: str) -> str:
    longest = max((len(match.group(0)) for match in re.finditer(r"`+", content)), default=0)
    return "`" * max(3, longest + 1)


class WikiCompiler:
    def __init__(self, settings: Settings, catalog: Catalog):
        self.settings = settings
        self.catalog = catalog
        self.wiki_dir = settings.home / "wiki"
        self.sources_dir = self.wiki_dir / "sources"

    def _bootstrap(self) -> None:
        self.sources_dir.mkdir(parents=True, exist_ok=True)
        agents = self.settings.home / "AGENTS.md"
        if not agents.exists():
            agents.write_text(
                "# Save Your Memory Wiki Contract\n\n"
                "- Treat the configured source tree as immutable.\n"
                "- Search `wiki/index.md` first, then read only relevant source pages.\n"
                "- Preserve absolute source paths and extraction uncertainty in answers.\n"
                "- Keep `wiki/log.md` append-only.\n",
                encoding="utf-8",
            )

    def _wiki_relative_path(self, record: CatalogRecord) -> str:
        return stable_wiki_path(record.relative_path)

    def _write_source_page(self, record: CatalogRecord, relative_wiki_path: str) -> None:
        destination = self.settings.home / relative_wiki_path
        lines = [
            "---",
            f"source_path: {_yaml_string(record.absolute_path)}",
            f"relative_path: {_yaml_string(record.relative_path)}",
            f"status: {_yaml_string(record.status)}",
            f"extractor: {_yaml_string(record.extractor)}",
            f"sha256: {_yaml_string(record.sha256)}",
            f"size_bytes: {record.size}",
            f"modified_ns: {record.mtime_ns}",
            f"indexed_at: {_yaml_string(record.indexed_at)}",
            "---",
            "",
            f"# {Path(record.relative_path).name}",
            "",
            "## Provenance",
            "",
            f"- Original file: `{record.absolute_path}`",
            f"- Relative path: `{record.relative_path}`",
            f"- Extraction status: `{record.status}`",
        ]
        if record.error:
            lines.append(f"- Extraction note: {record.error}")
        lines.extend(["", "## Extracted content", ""])
        if record.content:
            fence = _content_fence(record.content)
            lines.extend([f"{fence}text", record.content, fence, ""])
        else:
            lines.extend(
                [
                    "No readable text was extracted. The file remains indexed by path and metadata.",
                    "",
                ]
            )
        destination.write_text("\n".join(lines), encoding="utf-8")

    def _remove_generated_page(self, wiki_path: str) -> bool:
        candidate = (self.settings.home / wiki_path).resolve(strict=False)
        sources_root = self.sources_dir.resolve(strict=False)
        try:
            candidate.relative_to(sources_root)
        except ValueError:
            return False
        if candidate.is_file():
            candidate.unlink()
            return True
        return False

    def _write_navigation(self, records: Iterable[CatalogRecord]) -> None:
        files = [record for record in records if record.kind == "file"]
        directories = [record for record in records if record.kind == "directory"]
        index_lines = [
            "# Save Your Memory",
            "",
            f"Source root: `{self.settings.root}`",
            "",
            "## Source pages",
            "",
        ]
        if not files:
            index_lines.append("No files have been indexed.")
        for record in files:
            target = record.wiki_path.removeprefix("wiki/")
            index_lines.append(
                f"- [{record.relative_path}]({target}) — {record.status}, {record.size} bytes"
            )
        index_lines.extend(["", "## Directories", ""])
        index_lines.extend(f"- `{record.relative_path}/`" for record in directories)
        (self.wiki_dir / "index.md").write_text(
            "\n".join(index_lines) + "\n", encoding="utf-8"
        )

        counts: dict[str, int] = {}
        for record in files:
            counts[record.status] = counts.get(record.status, 0) + 1
        overview_lines = [
            "# Wiki Overview",
            "",
            f"- Source root: `{self.settings.root}`",
            f"- Indexed files: {len(files)}",
            f"- Indexed directories: {len(directories)}",
            "",
            "## Extraction status",
            "",
        ]
        overview_lines.extend(
            f"- {status}: {count}" for status, count in sorted(counts.items())
        )
        (self.wiki_dir / "overview.md").write_text(
            "\n".join(overview_lines) + "\n", encoding="utf-8"
        )

    def _append_log(self, sync: SyncSummary) -> None:
        log_path = self.wiki_dir / "log.md"
        if not log_path.exists():
            log_path.write_text("# Wiki Log\n", encoding="utf-8")
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with log_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(
                f"\n## {timestamp} — Ingest\n\n"
                f"- Added: {sync.added}\n"
                f"- Updated: {sync.updated}\n"
                f"- Unchanged: {sync.unchanged}\n"
                f"- Removed: {sync.removed}\n"
                f"- Extraction errors: {sync.extraction_errors}\n"
                f"- Scan errors: {len(sync.scan_errors)}\n"
            )

    def append_query(self, query: str, result_count: int) -> None:
        self._bootstrap()
        log_path = self.wiki_dir / "log.md"
        if not log_path.exists():
            log_path.write_text("# Wiki Log\n", encoding="utf-8")
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with log_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(
                f"\n## {timestamp} — Query\n\n"
                f"- Question: {query}\n"
                f"- Retrieved pages: {result_count}\n"
            )

    def compile(self, sync: SyncSummary) -> CompileSummary:
        self._bootstrap()
        removed_pages = sum(
            1 for wiki_path in sync.removed_wiki_paths if self._remove_generated_page(wiki_path)
        )
        changed = set(sync.changed_paths)
        source_pages = 0
        for metadata in self.catalog.iter_records(include_content=False):
            if metadata.kind != "file":
                continue
            wiki_path = self._wiki_relative_path(metadata)
            destination = self.settings.home / wiki_path
            if (
                metadata.relative_path in changed
                or metadata.wiki_path != wiki_path
                or not destination.exists()
            ):
                record = self.catalog.get(metadata.relative_path)
                if record is None:
                    continue
                self._write_source_page(record, wiki_path)
                source_pages += 1
        self._write_navigation(self.catalog.iter_records(include_content=False))
        self._append_log(sync)
        return CompileSummary(
            source_pages=source_pages,
            removed_pages=removed_pages,
            index_path=str(self.wiki_dir / "index.md"),
            log_path=str(self.wiki_dir / "log.md"),
        )

    def lint(self) -> LintReport:
        issues: list[str] = []
        for required in ("index.md", "overview.md", "log.md"):
            if not (self.wiki_dir / required).is_file():
                issues.append(f"Missing required wiki file: {required}")
        expected: set[Path] = set()
        for record in self.catalog.iter_records(include_content=False):
            if record.kind == "file" and record.wiki_path:
                page = (self.settings.home / record.wiki_path).resolve(strict=False)
                expected.add(page)
                if not page.is_file():
                    issues.append(f"Missing source page: {record.wiki_path}")
        if self.sources_dir.exists():
            for page in self.sources_dir.glob("*.md"):
                if page.resolve(strict=False) not in expected:
                    issues.append(f"Orphan source page: {page.name}")
        return LintReport(ok=not issues, issues=tuple(issues))
