import os
import tempfile
import tracemalloc
import unittest
from pathlib import Path

from save_your_memory.config import Settings
from save_your_memory.scanner import scan_tree
from save_your_memory.store import Catalog
from save_your_memory.wiki import WikiCompiler


class CatalogAndWikiTests(unittest.TestCase):
    def test_ingest_compiles_source_pages_and_searches_with_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "source"
            home = base / "memory"
            notes = root / "notes"
            notes.mkdir(parents=True)
            source = notes / "delta.md"
            source.write_text(
                "Project Delta launches in October. Owner: Mina.", encoding="utf-8"
            )
            (root / "archive.bin").write_bytes(b"\x00\x01")
            settings = Settings.load(
                env={
                    "SAVE_YOUR_MEMORY_ROOT": str(root),
                    "SAVE_YOUR_MEMORY_HOME": str(home),
                },
                env_file=None,
            )

            with Catalog(home / "index.sqlite3") as catalog:
                self.assertEqual(catalog.search_backend, "fts5")
                sync = catalog.sync(scan_tree(settings), settings.max_file_bytes)
                precompiled = catalog.get("notes/delta.md")
                self.assertTrue(precompiled.wiki_path.startswith("wiki/sources/"))
                compiled = WikiCompiler(settings, catalog).compile(sync)
                hits = catalog.search("Delta October", limit=3)
                records = {record.relative_path: record for record in catalog.records()}

            self.assertEqual(sync.added, 3)
            self.assertEqual(sync.updated, 0)
            self.assertEqual(sync.removed, 0)
            self.assertEqual(compiled.source_pages, 2)
            delta = records["notes/delta.md"]
            self.assertEqual(delta.status, "extracted")
            self.assertTrue(delta.wiki_path.startswith("wiki/sources/"))
            wiki_page = home / delta.wiki_path
            page_text = wiki_page.read_text(encoding="utf-8")
            self.assertIn(str(source.resolve()), page_text)
            self.assertIn("Project Delta launches in October", page_text)
            self.assertEqual(hits[0].relative_path, "notes/delta.md")
            self.assertEqual(hits[0].absolute_path, str(source.resolve()))
            self.assertEqual(hits[0].wiki_path, delta.wiki_path)
            self.assertIn("Delta", hits[0].excerpt)
            self.assertIn("notes/delta.md", (home / "wiki/index.md").read_text("utf-8"))
            self.assertIn("Ingest", (home / "wiki/log.md").read_text("utf-8"))

    def test_incremental_sync_updates_changed_removes_deleted_and_reuses_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "source"
            home = base / "memory"
            root.mkdir()
            changed = root / "changed.txt"
            removed = root / "removed.txt"
            stable = root / "stable.txt"
            changed.write_text("old value", encoding="utf-8")
            removed.write_text("remove me", encoding="utf-8")
            stable.write_text("keep me", encoding="utf-8")
            settings = Settings.load(
                env={
                    "SAVE_YOUR_MEMORY_ROOT": str(root),
                    "SAVE_YOUR_MEMORY_HOME": str(home),
                },
                env_file=None,
            )

            with Catalog(home / "index.sqlite3") as catalog:
                first = catalog.sync(scan_tree(settings), settings.max_file_bytes)
                WikiCompiler(settings, catalog).compile(first)
                removed_record = catalog.get("removed.txt")
                self.assertIsNotNone(removed_record)
                removed_page = home / removed_record.wiki_path
                self.assertTrue(removed_page.exists())

                changed.write_text("new value with evidence", encoding="utf-8")
                removed.unlink()
                second = catalog.sync(scan_tree(settings), settings.max_file_bytes)
                WikiCompiler(settings, catalog).compile(second)
                changed_record = catalog.get("changed.txt")

            self.assertEqual(second.added, 0)
            self.assertEqual(second.updated, 1)
            self.assertEqual(second.unchanged, 1)
            self.assertEqual(second.removed, 1)
            self.assertIn("new value with evidence", changed_record.content)
            self.assertFalse(removed_page.exists())

    def test_lint_reports_orphan_pages_and_missing_required_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "source"
            home = base / "memory"
            root.mkdir()
            (root / "note.txt").write_text("evidence", encoding="utf-8")
            settings = Settings.load(
                env={
                    "SAVE_YOUR_MEMORY_ROOT": str(root),
                    "SAVE_YOUR_MEMORY_HOME": str(home),
                },
                env_file=None,
            )
            with Catalog(home / "index.sqlite3") as catalog:
                sync = catalog.sync(scan_tree(settings), settings.max_file_bytes)
                compiler = WikiCompiler(settings, catalog)
                compiler.compile(sync)
                orphan = home / "wiki/sources/orphan.md"
                orphan.write_text("orphan", encoding="utf-8")
                (home / "wiki/overview.md").unlink()
                report = compiler.lint()

            self.assertFalse(report.ok)
            self.assertTrue(any("orphan.md" in issue for issue in report.issues))
            self.assertTrue(any("overview.md" in issue for issue in report.issues))

    def test_sync_rejects_file_replaced_by_symlink_after_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "source"
            home = base / "memory"
            root.mkdir()
            inside = root / "note.txt"
            outside = base / "outside.txt"
            inside.write_text("safe content", encoding="utf-8")
            outside.write_text("outside secret", encoding="utf-8")
            settings = Settings.load(
                env={
                    "SAVE_YOUR_MEMORY_ROOT": str(root),
                    "SAVE_YOUR_MEMORY_HOME": str(home),
                },
                env_file=None,
            )
            scanned = scan_tree(settings)
            inside.unlink()
            try:
                os.symlink(outside, inside)
            except OSError as exc:
                self.skipTest(f"file symlinks unavailable: {exc}")

            with Catalog(home / "index.sqlite3") as catalog:
                sync = catalog.sync(scanned, settings.max_file_bytes)
                record = catalog.get("note.txt")

            self.assertEqual(sync.extraction_errors, 1)
            self.assertEqual(record.status, "error")
            self.assertEqual(record.content, "")
            self.assertIn("link or reparse", record.error)
            self.assertNotIn("outside secret", record.content)
            self.assertEqual(record.absolute_path, str(inside.absolute()))
            self.assertTrue(Path(record.absolute_path).is_relative_to(settings.root))

    def test_new_files_do_not_delete_from_fts_but_updates_replace_existing_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "source"
            home = base / "memory"
            root.mkdir()
            source = root / "note.txt"
            source.write_text("first searchable value", encoding="utf-8")
            settings = Settings.load(
                env={
                    "SAVE_YOUR_MEMORY_ROOT": str(root),
                    "SAVE_YOUR_MEMORY_HOME": str(home),
                },
                env_file=None,
            )

            with Catalog(home / "index.sqlite3") as catalog:
                statements: list[str] = []
                catalog.connection.set_trace_callback(statements.append)
                catalog.sync(scan_tree(settings), settings.max_file_bytes)
                first_sync_deletes = [
                    statement
                    for statement in statements
                    if statement.upper().startswith("DELETE FROM CATALOG_SEARCH")
                ]

                source.write_text("second searchable value is longer", encoding="utf-8")
                statements.clear()
                catalog.sync(scan_tree(settings), settings.max_file_bytes)
                update_deletes = [
                    statement
                    for statement in statements
                    if statement.upper().startswith("DELETE FROM CATALOG_SEARCH")
                ]

            self.assertEqual(first_sync_deletes, [])
            self.assertEqual(len(update_deletes), 1)

    def test_compile_streams_large_catalog_without_materializing_all_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "source"
            home = base / "memory"
            root.mkdir()
            payload = "memory evidence " * 32_768
            for index in range(12):
                (root / f"large-{index}.txt").write_text(payload, encoding="utf-8")
            settings = Settings.load(
                env={
                    "SAVE_YOUR_MEMORY_ROOT": str(root),
                    "SAVE_YOUR_MEMORY_HOME": str(home),
                },
                env_file=None,
            )

            with Catalog(home / "index.sqlite3") as catalog:
                sync = catalog.sync(scan_tree(settings), settings.max_file_bytes)
                tracemalloc.start()
                WikiCompiler(settings, catalog).compile(sync)
                _, peak_bytes = tracemalloc.get_traced_memory()
                tracemalloc.stop()

            self.assertLess(peak_bytes, 5 * 1024 * 1024)

    def test_iter_records_uses_primary_key_order_without_global_nocase_sort(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "source"
            home = base / "memory"
            root.mkdir()
            for name in ("zeta.txt", "Alpha.txt", "middle.txt"):
                (root / name).write_text(name, encoding="utf-8")
            settings = Settings.load(
                env={
                    "SAVE_YOUR_MEMORY_ROOT": str(root),
                    "SAVE_YOUR_MEMORY_HOME": str(home),
                },
                env_file=None,
            )

            with Catalog(home / "index.sqlite3") as catalog:
                catalog.sync(scan_tree(settings), settings.max_file_bytes)
                comparisons = 0

                def count_nocase(left: str, right: str) -> int:
                    nonlocal comparisons
                    comparisons += 1
                    left_folded = left.casefold()
                    right_folded = right.casefold()
                    return (left_folded > right_folded) - (left_folded < right_folded)

                catalog.connection.create_collation("NOCASE", count_nocase)
                first = next(catalog.iter_records())

            self.assertEqual(first.relative_path, "Alpha.txt")
            self.assertEqual(comparisons, 0)

    def test_retry_unreadable_reextracts_unchanged_unsupported_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "source"
            home = base / "memory"
            root.mkdir()
            source = root / "legacy.bin"
            source.write_bytes(b"\x00binary")
            settings = Settings.load(
                env={
                    "SAVE_YOUR_MEMORY_ROOT": str(root),
                    "SAVE_YOUR_MEMORY_HOME": str(home),
                },
                env_file=None,
            )

            with Catalog(home / "index.sqlite3") as catalog:
                first = catalog.sync(scan_tree(settings), settings.max_file_bytes)
                original_mtime = source.stat().st_mtime_ns
                source.write_bytes(b"textual")
                os.utime(source, ns=(original_mtime, original_mtime))

                without_retry = catalog.sync(scan_tree(settings), settings.max_file_bytes)
                with_retry = catalog.sync(
                    scan_tree(settings),
                    settings.max_file_bytes,
                    retry_statuses=frozenset({"unsupported"}),
                )
                record = catalog.get("legacy.bin")

            self.assertEqual(first.added, 1)
            self.assertEqual(without_retry.unchanged, 1)
            self.assertEqual(with_retry.updated, 1)
            self.assertEqual(record.status, "extracted")
            self.assertEqual(record.content, "textual")

    def test_search_requires_core_terms_and_relaxes_question_words_without_or_noise(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "source"
            home = base / "memory"
            root.mkdir()
            (root / "answer.txt").write_text(
                "Project Aurora owner is Jisoo and deadline is Friday.",
                encoding="utf-8",
            )
            (root / "partial.txt").write_text(
                "Project planning notes without the requested subject.",
                encoding="utf-8",
            )
            settings = Settings.load(
                env={
                    "SAVE_YOUR_MEMORY_ROOT": str(root),
                    "SAVE_YOUR_MEMORY_HOME": str(home),
                },
                env_file=None,
            )

            with Catalog(home / "index.sqlite3") as catalog:
                catalog.sync(scan_tree(settings), settings.max_file_bytes)
                hits = catalog.search("Who owns Project Aurora?", limit=5)

            self.assertEqual([hit.relative_path for hit in hits], ["answer.txt"])

    def test_search_relaxes_long_natural_question_to_matching_named_terms(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "source"
            home = base / "memory"
            root.mkdir()
            (root / "mercedes.txt").write_text(
                "Data Builder Mercedes-Benz dimensionality curse training material.",
                encoding="utf-8",
            )
            (root / "generic.txt").write_text(
                "Data Builder generic course outline.", encoding="utf-8"
            )
            (root / "noise.txt").write_text(
                "This case does illustrate a dataset problem without the named subject.",
                encoding="utf-8",
            )
            settings = Settings.load(
                env={
                    "SAVE_YOUR_MEMORY_ROOT": str(root),
                    "SAVE_YOUR_MEMORY_HOME": str(home),
                },
                env_file=None,
            )

            with Catalog(home / "index.sqlite3") as catalog:
                catalog.sync(scan_tree(settings), settings.max_file_bytes)
                hits = catalog.search(
                    "In the Data Builder Mercedes-Benz case, what problem does the dataset illustrate?",
                    limit=5,
                )

            self.assertEqual([hit.relative_path for hit in hits], ["mercedes.txt"])


if __name__ == "__main__":
    unittest.main()
