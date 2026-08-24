from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Iterable, Iterator

from .extractors import ExtractionResult, extract_content
from .paths import stable_wiki_path
from .scanner import ScanResult


@dataclass(frozen=True)
class CatalogRecord:
    relative_path: str
    absolute_path: str
    kind: str
    size: int
    mtime_ns: int
    status: str
    extractor: str
    error: str
    sha256: str
    content: str
    wiki_path: str
    indexed_at: str


@dataclass(frozen=True)
class SyncSummary:
    added: int
    updated: int
    unchanged: int
    removed: int
    extraction_errors: int
    scan_errors: tuple[str, ...]
    changed_paths: tuple[str, ...]
    removed_wiki_paths: tuple[str, ...]


@dataclass(frozen=True)
class SearchHit:
    relative_path: str
    absolute_path: str
    wiki_path: str
    status: str
    excerpt: str
    score: float


class CatalogError(RuntimeError):
    """Raised when the required local catalog capability is unavailable."""


class Catalog:
    QUERY_STOPWORDS = frozenset(
        {
            "a",
            "an",
            "and",
            "are",
            "case",
            "dataset",
            "does",
            "find",
            "for",
            "how",
            "in",
            "is",
            "illustrate",
            "me",
            "of",
            "please",
            "problem",
            "show",
            "tell",
            "the",
            "to",
            "what",
            "when",
            "where",
            "who",
            "why",
            "누구",
            "누구야",
            "뭐야",
            "무엇",
            "어디",
            "언제",
            "왜",
            "어떻게",
            "알려줘",
            "찾아줘",
            "보여줘",
        }
    )

    def __init__(self, database_path: Path):
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path = database_path
        self.connection = sqlite3.connect(database_path)
        self.connection.row_factory = sqlite3.Row
        self.search_backend = "fts5"
        try:
            self._initialize()
        except Exception:
            self.connection.close()
            raise

    def __enter__(self) -> "Catalog":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def _initialize(self) -> None:
        self.connection.executescript(
            """
            PRAGMA journal_mode = WAL;
            CREATE TABLE IF NOT EXISTS catalog_entries (
                relative_path TEXT PRIMARY KEY,
                absolute_path TEXT NOT NULL,
                kind TEXT NOT NULL,
                size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                status TEXT NOT NULL,
                extractor TEXT NOT NULL,
                error TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                content TEXT NOT NULL,
                wiki_path TEXT NOT NULL DEFAULT '',
                indexed_at TEXT NOT NULL
            );
            """
        )
        try:
            self.connection.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS catalog_search "
                "USING fts5(relative_path, content, tokenize='unicode61')"
            )
        except sqlite3.OperationalError as exc:
            raise CatalogError(
                "SQLite FTS5 support is required for ranked wiki retrieval"
            ) from exc
        self.connection.commit()

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> CatalogRecord:
        payload = dict(row)
        if payload["kind"] == "file":
            payload["wiki_path"] = stable_wiki_path(payload["relative_path"])
        return CatalogRecord(**payload)

    def records(self) -> tuple[CatalogRecord, ...]:
        return tuple(self.iter_records())

    def iter_records(self, *, include_content: bool = True) -> Iterator[CatalogRecord]:
        content_column = "content" if include_content else "'' AS content"
        rows = self.connection.execute(
            f"""
            SELECT relative_path, absolute_path, kind, size, mtime_ns, status,
                   extractor, error, sha256, {content_column}, wiki_path, indexed_at
            FROM catalog_entries
            ORDER BY relative_path
            """
        )
        for row in rows:
            yield self._record_from_row(row)

    def get(self, relative_path: str) -> CatalogRecord | None:
        row = self.connection.execute(
            "SELECT * FROM catalog_entries WHERE relative_path = ?", (relative_path,)
        ).fetchone()
        return self._record_from_row(row) if row is not None else None

    def _replace_search(
        self, relative_path: str, content: str, *, replace_existing: bool
    ) -> None:
        if replace_existing:
            self.connection.execute(
                "DELETE FROM catalog_search WHERE relative_path = ?", (relative_path,)
            )
        if content:
            self.connection.execute(
                "INSERT INTO catalog_search(relative_path, content) VALUES (?, ?)",
                (relative_path, content),
            )

    def sync(
        self,
        scan: ScanResult,
        max_file_bytes: int,
        *,
        retry_statuses: frozenset[str] = frozenset(),
    ) -> SyncSummary:
        existing = {
            record.relative_path: record
            for record in self.iter_records(include_content=False)
        }
        seen: set[str] = set()
        changed_paths: list[str] = []
        added = updated = unchanged = extraction_errors = 0
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        for entry in scan.entries:
            relative_path = entry.relative_path.as_posix()
            seen.add(relative_path)
            previous = existing.get(relative_path)
            metadata_unchanged = (
                previous is not None
                and previous.kind == entry.kind
                and previous.size == entry.size
                and previous.mtime_ns == entry.mtime_ns
            )
            retry_requested = (
                metadata_unchanged and previous.status in retry_statuses
            )
            if metadata_unchanged and not retry_requested:
                unchanged += 1
                continue

            if entry.kind == "file":
                extraction = extract_content(
                    entry.absolute_path,
                    max_file_bytes,
                    expected_size=entry.size,
                    expected_mtime_ns=entry.mtime_ns,
                )
            else:
                extraction = ExtractionResult("directory", "", "", "", "")
            if extraction.status == "error":
                extraction_errors += 1
            if (
                retry_requested
                and previous.status == extraction.status
                and previous.extractor == extraction.extractor
                and previous.error == extraction.error
                and previous.sha256 == extraction.sha256
                and previous.content == extraction.content
            ):
                unchanged += 1
                continue

            wiki_path = previous.wiki_path if previous is not None else ""
            self.connection.execute(
                """
                INSERT INTO catalog_entries (
                    relative_path, absolute_path, kind, size, mtime_ns, status,
                    extractor, error, sha256, content, wiki_path, indexed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(relative_path) DO UPDATE SET
                    absolute_path=excluded.absolute_path,
                    kind=excluded.kind,
                    size=excluded.size,
                    mtime_ns=excluded.mtime_ns,
                    status=excluded.status,
                    extractor=excluded.extractor,
                    error=excluded.error,
                    sha256=excluded.sha256,
                    content=excluded.content,
                    indexed_at=excluded.indexed_at
                """,
                (
                    relative_path,
                    str(entry.absolute_path.absolute()),
                    entry.kind,
                    entry.size,
                    entry.mtime_ns,
                    extraction.status,
                    extraction.extractor,
                    extraction.error,
                    extraction.sha256,
                    extraction.content,
                    wiki_path,
                    now,
                ),
            )
            self._replace_search(
                relative_path,
                extraction.content if extraction.status == "extracted" else "",
                replace_existing=(
                    previous is not None and previous.status == "extracted"
                ),
            )
            changed_paths.append(relative_path)
            if previous is None:
                added += 1
            else:
                updated += 1

        removed_records = [
            record for path, record in existing.items() if path not in seen
        ]
        for record in removed_records:
            self.connection.execute(
                "DELETE FROM catalog_entries WHERE relative_path = ?",
                (record.relative_path,),
            )
            self.connection.execute(
                "DELETE FROM catalog_search WHERE relative_path = ?",
                (record.relative_path,),
            )
        self.connection.commit()

        return SyncSummary(
            added=added,
            updated=updated,
            unchanged=unchanged,
            removed=len(removed_records),
            extraction_errors=extraction_errors,
            scan_errors=scan.errors,
            changed_paths=tuple(changed_paths),
            removed_wiki_paths=tuple(
                record.wiki_path for record in removed_records if record.wiki_path
            ),
        )

    def set_wiki_path(self, relative_path: str, wiki_path: str) -> None:
        self.set_wiki_paths(((relative_path, wiki_path),))

    def set_wiki_paths(self, updates: Iterable[tuple[str, str]]) -> None:
        self.connection.executemany(
            "UPDATE catalog_entries SET wiki_path = ? WHERE relative_path = ?",
            ((wiki_path, relative_path) for relative_path, wiki_path in updates),
        )
        self.connection.commit()

    @staticmethod
    def _query_tokens(query: str) -> list[str]:
        tokens: list[str] = []
        seen: set[str] = set()
        for token in re.findall(r"[\w]+(?:-[\w]+)*", query, flags=re.UNICODE):
            folded = token.casefold()
            if folded in Catalog.QUERY_STOPWORDS or folded in seen:
                continue
            seen.add(folded)
            tokens.append(token)
        return tokens

    def search(self, query: str, limit: int = 5) -> tuple[SearchHit, ...]:
        tokens = self._query_tokens(query)
        if not tokens or limit <= 0:
            return ()
        candidate_tokens = tokens[:4] + tokens[-4:] if len(tokens) > 8 else tokens
        minimum_terms = 1 if len(candidate_tokens) == 1 else 2
        for term_count in range(len(candidate_tokens), minimum_terms - 1, -1):
            for term_group in combinations(candidate_tokens, term_count):
                match_query = " AND ".join(f'"{token}"' for token in term_group)
                rows = self.connection.execute(
                    """
                    SELECT e.relative_path, e.absolute_path, e.wiki_path, e.status,
                           snippet(catalog_search, 1, '', '', ' … ', 28) AS excerpt,
                           bm25(catalog_search) AS rank
                    FROM catalog_search
                    JOIN catalog_entries e USING(relative_path)
                    WHERE catalog_search MATCH ?
                    ORDER BY rank, e.relative_path COLLATE NOCASE
                    LIMIT ?
                    """,
                    (match_query, limit),
                ).fetchall()
                if rows:
                    return tuple(
                        SearchHit(
                            relative_path=row["relative_path"],
                            absolute_path=row["absolute_path"],
                            wiki_path=stable_wiki_path(row["relative_path"]),
                            status=row["status"],
                            excerpt=row["excerpt"],
                            score=float(-row["rank"]),
                        )
                        for row in rows
                    )
        return ()
