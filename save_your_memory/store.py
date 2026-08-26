from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from .config import DEFAULT_CHUNK_BYTES
from .extractors import ExtractionResult, extract_content
from .paths import stable_wiki_path
from .scanner import ScanResult


@dataclass(frozen=True)
class CatalogRecord:
    id: int
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


_CJK_RUN = re.compile(
    r"[\u1100-\u11ff\u3130-\u318f\ua960-\ua97f\uac00-\ud7a3"
    r"\u2e80-\u9fff\uf900-\ufaff]+"
)


def cjk_ngrams(text: str) -> str:
    """Project CJK text into space-joined contiguous character bigrams."""
    grams: list[str] = []
    for run in _CJK_RUN.findall(text):
        grams.extend(run[index : index + 2] for index in range(len(run) - 1))
    return " ".join(grams)


def _cjk_query_bigrams(token: str) -> tuple[str, ...]:
    """Non-overlapping bigrams so spaced source text can satisfy glued queries."""
    return tuple(
        run[start : start + 2]
        for run in _CJK_RUN.findall(token)
        for start in range(0, len(run) - 1, 2)
    )


class Catalog:
    SCHEMA_VERSION = 4
    UPGRADABLE_SCHEMA_VERSIONS = frozenset({2, 3})
    BM25_COLUMN_WEIGHTS = "8.0, 4.0, 2.0"
    DEMOTED_STATUSES = ("unsupported", "error")
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

    def __init__(self, database_path: Path, *, chunk_bytes: int = DEFAULT_CHUNK_BYTES):
        if chunk_bytes <= 0:
            raise ValueError("chunk_bytes must be greater than zero")
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path = database_path
        self.chunk_bytes = chunk_bytes
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
        version_row = self.connection.execute("PRAGMA user_version").fetchone()
        version = int(version_row[0]) if version_row is not None else 0
        entries_exist = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='catalog_entries'"
        ).fetchone()
        supported_versions = self.UPGRADABLE_SCHEMA_VERSIONS | {self.SCHEMA_VERSION}
        if entries_exist is not None and version not in supported_versions:
            raise CatalogError(
                "A legacy save-your-memory catalog was found. Preserve or remove the "
                f"generated index.sqlite3, then rerun index to rebuild schema "
                f"version {self.SCHEMA_VERSION}."
            )
        if version not in ({0} | supported_versions):
            raise CatalogError(
                f"Unsupported catalog schema version {version}; rebuild index.sqlite3"
            )
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS catalog_entries (
                id INTEGER PRIMARY KEY,
                relative_path TEXT NOT NULL UNIQUE,
                absolute_path TEXT NOT NULL,
                kind TEXT NOT NULL,
                size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                status TEXT NOT NULL,
                extractor TEXT NOT NULL,
                error TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                wiki_path TEXT NOT NULL DEFAULT '',
                indexed_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS catalog_chunks (
                id INTEGER PRIMARY KEY,
                entry_id INTEGER NOT NULL REFERENCES catalog_entries(id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                relative_path TEXT NOT NULL,
                content TEXT NOT NULL,
                ngrams TEXT NOT NULL DEFAULT '',
                UNIQUE(entry_id, chunk_index)
            );
            """
        )
        if entries_exist is not None and version != self.SCHEMA_VERSION:
            self._migrate_legacy(version)
        self._create_search_index()
        self.connection.execute(f"PRAGMA user_version = {self.SCHEMA_VERSION}")
        self.connection.commit()

    def _create_search_index(self) -> None:
        try:
            self.connection.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS catalog_search
                USING fts5(
                    relative_path,
                    content,
                    ngrams,
                    content='catalog_chunks',
                    content_rowid='id',
                    tokenize='unicode61'
                )
                """
            )
        except sqlite3.OperationalError as exc:
            raise CatalogError(
                "SQLite FTS5 support is required for ranked wiki retrieval"
            ) from exc
        self.connection.executescript(
            """
            CREATE TRIGGER IF NOT EXISTS catalog_chunks_ai
            AFTER INSERT ON catalog_chunks
            BEGIN
                INSERT INTO catalog_search(rowid, relative_path, content, ngrams)
                VALUES (new.id, new.relative_path, new.content, new.ngrams);
            END;

            CREATE TRIGGER IF NOT EXISTS catalog_chunks_ad
            AFTER DELETE ON catalog_chunks
            BEGIN
                INSERT INTO catalog_search(catalog_search, rowid, relative_path, content, ngrams)
                VALUES('delete', old.id, old.relative_path, old.content, old.ngrams);
            END;

            CREATE TRIGGER IF NOT EXISTS catalog_chunks_au
            AFTER UPDATE ON catalog_chunks
            BEGIN
                INSERT INTO catalog_search(catalog_search, rowid, relative_path, content, ngrams)
                VALUES('delete', old.id, old.relative_path, old.content, old.ngrams);
                INSERT INTO catalog_search(rowid, relative_path, content, ngrams)
                VALUES (new.id, new.relative_path, new.content, new.ngrams);
            END;
            """
        )

    def _migrate_legacy(self, version: int) -> None:
        chunk_columns = {
            row[1]
            for row in self.connection.execute("PRAGMA table_info(catalog_chunks)")
        }
        if "ngrams" not in chunk_columns:
            self.connection.execute(
                "ALTER TABLE catalog_chunks ADD COLUMN ngrams TEXT NOT NULL DEFAULT ''"
            )
        if version == 2:
            missing = self.connection.execute(
                """
                SELECT e.id, e.relative_path
                FROM catalog_entries e
                WHERE e.kind = 'file'
                  AND NOT EXISTS (
                      SELECT 1 FROM catalog_chunks c WHERE c.entry_id = e.id
                  )
                """
            ).fetchall()
            self.connection.executemany(
                """
                INSERT INTO catalog_chunks(entry_id, chunk_index, relative_path, content, ngrams)
                VALUES (?, 0, ?, '', ?)
                """,
                (
                    (
                        row["id"],
                        row["relative_path"],
                        cjk_ngrams(row["relative_path"]),
                    )
                    for row in missing
                ),
            )
        # Rebuild the FTS5 index so the ngrams column becomes searchable.
        self.connection.executescript(
            """
            DROP TRIGGER IF EXISTS catalog_chunks_ai;
            DROP TRIGGER IF EXISTS catalog_chunks_ad;
            DROP TRIGGER IF EXISTS catalog_chunks_au;
            DROP TABLE IF EXISTS catalog_search;
            """
        )
        backfill = []
        for row in self.connection.execute(
            "SELECT id, chunk_index, relative_path, content FROM catalog_chunks"
        ):
            grams = " ".join(
                part
                for part in (
                    cjk_ngrams(row["content"]),
                    cjk_ngrams(row["relative_path"]) if row["chunk_index"] == 0 else "",
                )
                if part
            )
            backfill.append((grams, row["id"]))
        self.connection.executemany(
            "UPDATE catalog_chunks SET ngrams = ? WHERE id = ?",
            backfill,
        )
        self._create_search_index()
        self.connection.execute(
            """
            INSERT INTO catalog_search(rowid, relative_path, content, ngrams)
            SELECT id, relative_path, content, ngrams FROM catalog_chunks
            """
        )

    @staticmethod
    def _record_from_row(row: sqlite3.Row, content: str = "") -> CatalogRecord:
        payload = dict(row)
        if payload["kind"] == "file":
            payload["wiki_path"] = stable_wiki_path(payload["relative_path"])
        payload["content"] = content
        return CatalogRecord(**payload)

    def _content_for_entry(self, entry_id: int) -> str:
        rows = self.connection.execute(
            """
            SELECT content
            FROM catalog_chunks
            WHERE entry_id = ?
            ORDER BY chunk_index, id
            """,
            (entry_id,),
        )
        return "".join(row["content"] for row in rows)

    @classmethod
    def _split_long_piece(cls, piece: str, chunk_bytes: int) -> tuple[str, ...]:
        if len(piece.encode("utf-8")) <= chunk_bytes:
            return (piece,)
        chunks: list[str] = []
        current: list[str] = []
        current_bytes = 0
        for unit in re.findall(r"\S+\s*|\s+", piece):
            unit_bytes = len(unit.encode("utf-8"))
            if unit_bytes > chunk_bytes:
                if current:
                    chunks.append("".join(current))
                    current = []
                    current_bytes = 0
                chunks.append(unit)
                continue
            if current and current_bytes + unit_bytes > chunk_bytes:
                chunks.append("".join(current))
                current = [unit]
                current_bytes = unit_bytes
                continue
            current.append(unit)
            current_bytes += unit_bytes
        if current:
            chunks.append("".join(current))
        return tuple(chunks)

    @classmethod
    def _chunk_content(cls, content: str, chunk_bytes: int) -> tuple[str, ...]:
        if not content:
            return ()
        chunks: list[str] = []
        current: list[str] = []
        current_bytes = 0
        for piece in content.splitlines(keepends=True):
            piece_bytes = len(piece.encode("utf-8"))
            if piece_bytes > chunk_bytes:
                if current:
                    chunks.append("".join(current))
                    current = []
                    current_bytes = 0
                chunks.extend(cls._split_long_piece(piece, chunk_bytes))
                continue
            if current and current_bytes + piece_bytes > chunk_bytes:
                chunks.append("".join(current))
                current = [piece]
                current_bytes = piece_bytes
                continue
            current.append(piece)
            current_bytes += piece_bytes
        if current:
            chunks.append("".join(current))
        return tuple(chunk for chunk in chunks if chunk)

    def _delete_chunks(self, entry_id: int) -> None:
        self.connection.execute(
            "DELETE FROM catalog_chunks WHERE entry_id = ?",
            (entry_id,),
        )

    def _write_chunks(self, entry_id: int, relative_path: str, content: str) -> None:
        chunks = self._chunk_content(content, self.chunk_bytes)
        if not chunks:
            chunks = ("",)
        path_grams = cjk_ngrams(relative_path)
        self.connection.executemany(
            """
            INSERT INTO catalog_chunks(entry_id, chunk_index, relative_path, content, ngrams)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                (
                    entry_id,
                    index,
                    relative_path if index == 0 else "",
                    chunk,
                    " ".join(
                        part
                        for part in (cjk_ngrams(chunk), path_grams if index == 0 else "")
                        if part
                    ),
                )
                for index, chunk in enumerate(chunks)
            ),
        )

    def records(self) -> tuple[CatalogRecord, ...]:
        return tuple(self.iter_records())

    def iter_records(self, *, include_content: bool = True) -> Iterator[CatalogRecord]:
        rows = self.connection.execute(
            """
            SELECT id, relative_path, absolute_path, kind, size, mtime_ns, status,
                   extractor, error, sha256, wiki_path, indexed_at
            FROM catalog_entries
            ORDER BY relative_path
            """
        )
        for row in rows:
            content = ""
            if include_content and row["kind"] == "file":
                content = self._content_for_entry(row["id"])
            yield self._record_from_row(row, content=content)

    def get(self, relative_path: str) -> CatalogRecord | None:
        row = self.connection.execute(
            """
            SELECT id, relative_path, absolute_path, kind, size, mtime_ns, status,
                   extractor, error, sha256, wiki_path, indexed_at
            FROM catalog_entries
            WHERE relative_path = ?
            """,
            (relative_path,),
        ).fetchone()
        if row is None:
            return None
        content = self._content_for_entry(row["id"]) if row["kind"] == "file" else ""
        return self._record_from_row(row, content=content)

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
            retry_requested = metadata_unchanged and previous.status in retry_statuses
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
            ):
                unchanged += 1
                continue

            wiki_path = previous.wiki_path if previous is not None else ""
            self.connection.execute(
                """
                INSERT INTO catalog_entries (
                    relative_path, absolute_path, kind, size, mtime_ns, status,
                    extractor, error, sha256, wiki_path, indexed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(relative_path) DO UPDATE SET
                    absolute_path=excluded.absolute_path,
                    kind=excluded.kind,
                    size=excluded.size,
                    mtime_ns=excluded.mtime_ns,
                    status=excluded.status,
                    extractor=excluded.extractor,
                    error=excluded.error,
                    sha256=excluded.sha256,
                    wiki_path=excluded.wiki_path,
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
                    wiki_path,
                    now,
                ),
            )
            entry_row = self.connection.execute(
                "SELECT id FROM catalog_entries WHERE relative_path = ?",
                (relative_path,),
            ).fetchone()
            if entry_row is None:
                raise CatalogError(f"Failed to fetch catalog row for {relative_path}")
            entry_id = int(entry_row["id"])
            self._delete_chunks(entry_id)
            if entry.kind == "file":
                content = extraction.content if extraction.status == "extracted" else ""
                self._write_chunks(entry_id, relative_path, content)
            changed_paths.append(relative_path)
            if previous is None:
                added += 1
            else:
                updated += 1

        removed_records = [record for path, record in existing.items() if path not in seen]
        for record in removed_records:
            self._delete_chunks(record.id)
            self.connection.execute(
                "DELETE FROM catalog_entries WHERE relative_path = ?",
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

    @staticmethod
    def _excerpt(content: str, tokens: Iterable[str], max_characters: int = 320) -> str:
        if not content:
            return ""
        folded = content.casefold()
        positions = [
            position
            for token in tokens
            if (position := folded.find(token.casefold())) >= 0
        ]
        anchor = min(positions, default=0)
        start = max(0, anchor - max_characters // 3)
        end = min(len(content), start + max_characters)
        if end - start < max_characters:
            start = max(0, end - max_characters)
        excerpt = re.sub(r"\s+", " ", content[start:end]).strip()
        if start > 0:
            excerpt = f"… {excerpt}"
        if end < len(content):
            excerpt = f"{excerpt} …"
        return excerpt

    def _demotion_clause(self) -> str:
        statuses = ", ".join(f"'{status}'" for status in self.DEMOTED_STATUSES)
        return f"CASE WHEN e.status IN ({statuses}) THEN 1 ELSE 0 END"

    def _search_same_chunk(
        self, match_query: str, limit: int
    ) -> list[sqlite3.Row]:
        return self.connection.execute(
            f"""
            WITH matched AS (
                SELECT rowid,
                       bm25(catalog_search, {self.BM25_COLUMN_WEIGHTS}) AS rank
                FROM catalog_search
                WHERE catalog_search MATCH '{match_query}'
            ),
            ranked_chunks AS (
                SELECT c.entry_id, c.content, matched.rank AS rank,
                       row_number() OVER (
                           PARTITION BY c.entry_id
                           ORDER BY matched.rank, c.chunk_index, c.id
                       ) AS hit_order
                FROM matched
                JOIN catalog_chunks c ON c.id = matched.rowid
            )
            SELECT e.relative_path, e.absolute_path, e.wiki_path, e.status,
                   ranked_chunks.content, ranked_chunks.rank
            FROM ranked_chunks
            JOIN catalog_entries e ON e.id = ranked_chunks.entry_id
            WHERE ranked_chunks.hit_order = 1
            ORDER BY {self._demotion_clause()},
                     ranked_chunks.rank, e.relative_path COLLATE NOCASE
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def _search_across_chunks(
        self, clauses: Sequence[str], limit: int
    ) -> list[sqlite3.Row]:
        if len(clauses) < 2:
            return []
        ctes = [
            f"""
            term_0_ranked AS (
                SELECT c.entry_id, c.content,
                       catalog_search.rank AS chunk_rank,
                       row_number() OVER (
                           PARTITION BY c.entry_id
                           ORDER BY catalog_search.rank, c.chunk_index, c.id
                       ) AS hit_order
                FROM catalog_search
                JOIN catalog_chunks c ON c.id = catalog_search.rowid
                WHERE catalog_search MATCH '{clauses[0]}'
            ),
            term_0 AS (
                SELECT entry_id, content, chunk_rank AS rank
                FROM term_0_ranked
                WHERE hit_order = 1
            )
            """
        ]
        for index, clause in enumerate(clauses[1:], start=1):
            ctes.append(
                f"""
                term_{index} AS (
                    SELECT c.entry_id, min(catalog_search.rank) AS rank
                    FROM catalog_search
                    JOIN catalog_chunks c ON c.id = catalog_search.rowid
                    WHERE catalog_search MATCH '{clause}'
                    GROUP BY c.entry_id
                )
                """
            )
        joins = "\n".join(
            f"JOIN term_{index} ON term_{index}.entry_id = term_0.entry_id"
            for index in range(1, len(clauses))
        )
        combined_rank = " + ".join(
            f"term_{index}.rank" for index in range(len(clauses))
        )
        sql = f"""
            WITH {','.join(ctes)}
            SELECT e.relative_path, e.absolute_path, e.wiki_path, e.status,
                   term_0.content, ({combined_rank}) AS rank
            FROM term_0
            {joins}
            JOIN catalog_entries e ON e.id = term_0.entry_id
            ORDER BY {self._demotion_clause()},
                     rank, e.relative_path COLLATE NOCASE
            LIMIT ?
        """
        return self.connection.execute(sql, (limit,)).fetchall()

    @staticmethod
    def _match_clause(token: str) -> str:
        grams = _cjk_query_bigrams(token)
        if len(grams) < 2:
            return f'"{token}"'
        bigram_match = " AND ".join(f'ngrams: "{gram}"' for gram in grams)
        return f'("{token}" OR {bigram_match})'

    def search(self, query: str, limit: int = 5) -> tuple[SearchHit, ...]:
        tokens = self._query_tokens(query)
        if not tokens or limit <= 0:
            return ()
        candidate_tokens = tokens[:4] + tokens[-4:] if len(tokens) > 8 else tokens
        minimum_terms = 1 if len(candidate_tokens) == 1 else 2
        ladder_rows: list[sqlite3.Row] | None = None
        matched_terms: tuple[str, ...] = candidate_tokens
        for term_count in range(len(candidate_tokens), minimum_terms - 1, -1):
            for term_group in combinations(candidate_tokens, term_count):
                clauses = [self._match_clause(token) for token in term_group]
                match_query = " AND ".join(clauses)
                rows = self._search_same_chunk(match_query, limit)
                if len(rows) < limit:
                    seen_paths = {row["relative_path"] for row in rows}
                    rows.extend(
                        row
                        for row in self._search_across_chunks(clauses, limit)
                        if row["relative_path"] not in seen_paths
                    )
                    rows = rows[:limit]
                if rows:
                    ladder_rows = rows[:limit]
                    matched_terms = term_group
                    break
            if ladder_rows is not None:
                break
        rows = ladder_rows
        if rows is None:
            fallback_query = " OR ".join(
                self._match_clause(token) for token in candidate_tokens
            )
            rows = self._search_same_chunk(fallback_query, limit)[:limit]
        if not rows:
            return ()
        return tuple(
                SearchHit(
                    relative_path=row["relative_path"],
                    absolute_path=row["absolute_path"],
                    wiki_path=stable_wiki_path(row["relative_path"]),
                    status=row["status"],
                    excerpt=(
                        self._excerpt(row["content"], matched_terms)
                        or row["relative_path"]
                    ),
                    score=float(-row["rank"]),
                )
                for row in rows
        )
