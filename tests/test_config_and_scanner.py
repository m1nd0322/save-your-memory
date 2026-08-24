import os
import tempfile
import unittest
from pathlib import Path

from save_your_memory.config import ConfigurationError, Settings
from save_your_memory.scanner import scan_tree


class SettingsTests(unittest.TestCase):
    def test_process_environment_overrides_dotenv_and_resolves_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            dotenv_root = base / "dotenv-root"
            process_root = base / "process-root"
            home = base / "wiki-home"
            dotenv_root.mkdir()
            process_root.mkdir()
            env_file = base / ".env"
            env_file.write_text(
                f'SAVE_YOUR_MEMORY_ROOT="{dotenv_root}"\n'
                "SAVE_YOUR_MEMORY_MAX_FILE_BYTES=123\n",
                encoding="utf-8",
            )

            settings = Settings.load(
                env={
                    "SAVE_YOUR_MEMORY_ROOT": str(process_root),
                    "SAVE_YOUR_MEMORY_HOME": str(home),
                    "SAVE_YOUR_MEMORY_MAX_FILE_BYTES": "456",
                },
                env_file=env_file,
            )

            self.assertEqual(settings.root, process_root.resolve())
            self.assertEqual(settings.home, home.resolve())
            self.assertEqual(settings.max_file_bytes, 456)

    def test_missing_or_invalid_root_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "SAVE_YOUR_MEMORY_ROOT"):
            Settings.load(env={}, env_file=None)

        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / "missing"
            with self.assertRaisesRegex(ConfigurationError, "does not exist"):
                Settings.load(
                    env={"SAVE_YOUR_MEMORY_ROOT": str(missing)}, env_file=None
                )

    def test_output_home_cannot_equal_source_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(ConfigurationError, "must differ"):
                Settings.load(
                    env={
                        "SAVE_YOUR_MEMORY_ROOT": str(root),
                        "SAVE_YOUR_MEMORY_HOME": str(root),
                    },
                    env_file=None,
                )


class ScannerTests(unittest.TestCase):
    def test_scans_nested_directories_and_files_but_excludes_generated_trees(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "source"
            home = root / "generated"
            nested = root / "notes" / "deep"
            nested.mkdir(parents=True)
            home.mkdir()
            (root / "top.txt").write_text("top", encoding="utf-8")
            (nested / "detail.md").write_text("detail", encoding="utf-8")
            (home / "must-not-index.md").write_text("generated", encoding="utf-8")
            (root / ".git").mkdir()
            (root / ".git" / "config").write_text("secret", encoding="utf-8")

            settings = Settings.load(
                env={
                    "SAVE_YOUR_MEMORY_ROOT": str(root),
                    "SAVE_YOUR_MEMORY_HOME": str(home),
                },
                env_file=None,
            )
            result = scan_tree(settings)
            paths = {entry.relative_path.as_posix(): entry.kind for entry in result.entries}

            self.assertEqual(paths["top.txt"], "file")
            self.assertEqual(paths["notes"], "directory")
            self.assertEqual(paths["notes/deep"], "directory")
            self.assertEqual(paths["notes/deep/detail.md"], "file")
            self.assertNotIn("generated", paths)
            self.assertNotIn(".git", paths)
            self.assertEqual(result.errors, ())

    def test_does_not_follow_directory_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "source"
            outside = base / "outside"
            root.mkdir()
            outside.mkdir()
            (outside / "outside.txt").write_text("outside", encoding="utf-8")
            link = root / "linked"
            try:
                os.symlink(outside, link, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlinks unavailable: {exc}")

            settings = Settings.load(
                env={
                    "SAVE_YOUR_MEMORY_ROOT": str(root),
                    "SAVE_YOUR_MEMORY_HOME": str(base / "home"),
                },
                env_file=None,
            )
            result = scan_tree(settings)
            paths = {entry.relative_path.as_posix() for entry in result.entries}

            self.assertNotIn("linked/outside.txt", paths)


if __name__ == "__main__":
    unittest.main()
