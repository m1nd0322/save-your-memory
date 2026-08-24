import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "save_your_memory", *arguments],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=30,
        )

    def test_index_query_status_and_lint_json_flow(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "source"
            home = base / "memory"
            root.mkdir()
            source = root / "plan.md"
            source.write_text(
                "Project Aurora owner is Jisoo. The deadline is Friday.",
                encoding="utf-8",
            )
            env_file = base / ".env"
            env_file.write_text(
                f'SAVE_YOUR_MEMORY_ROOT="{root}"\n'
                f'SAVE_YOUR_MEMORY_HOME="{home}"\n',
                encoding="utf-8",
            )

            indexed = self.run_cli("--env-file", str(env_file), "index", "--json")
            self.assertEqual(indexed.returncode, 0, indexed.stderr)
            index_payload = json.loads(indexed.stdout)
            self.assertEqual(index_payload["command"], "index")
            self.assertEqual(index_payload["sync"]["added"], 1)
            self.assertNotIn("changed_paths", index_payload["sync"])
            self.assertEqual(index_payload["sync"]["changed_path_count"], 1)
            self.assertEqual(index_payload["sync"]["changed_path_sample"], ["plan.md"])
            self.assertTrue(Path(index_payload["wiki_index"]).is_file())

            queried = self.run_cli(
                "--env-file",
                str(env_file),
                "query",
                "Who owns Project Aurora?",
                "--limit",
                "3",
                "--json",
            )
            self.assertEqual(queried.returncode, 0, queried.stderr)
            query_payload = json.loads(queried.stdout)
            self.assertEqual(query_payload["command"], "query")
            self.assertEqual(query_payload["results"][0]["relative_path"], "plan.md")
            self.assertEqual(
                query_payload["results"][0]["source_path"], str(source.resolve())
            )
            self.assertTrue(Path(query_payload["results"][0]["wiki_path"]).is_file())
            self.assertIn("Jisoo", query_payload["results"][0]["excerpt"])

            status = self.run_cli("--env-file", str(env_file), "status", "--json")
            self.assertEqual(status.returncode, 0, status.stderr)
            status_payload = json.loads(status.stdout)
            self.assertEqual(status_payload["files"], 1)
            self.assertEqual(status_payload["extraction_status"]["extracted"], 1)

            linted = self.run_cli("--env-file", str(env_file), "lint", "--json")
            self.assertEqual(linted.returncode, 0, linted.stderr)
            self.assertEqual(json.loads(linted.stdout), {"command": "lint", "issues": [], "ok": True})
            self.assertIn("Query", (home / "wiki/log.md").read_text(encoding="utf-8"))

    def test_missing_configuration_returns_actionable_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            missing_env = Path(temp) / "missing.env"
            completed = self.run_cli(
                "--env-file", str(missing_env), "status", "--json"
            )

            self.assertEqual(completed.returncode, 2)
            self.assertIn("SAVE_YOUR_MEMORY_ROOT", completed.stderr)
            self.assertEqual(completed.stdout, "")

    def test_json_output_is_utf8_safe_for_korean_query_and_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "source"
            home = base / "memory"
            root.mkdir()
            (root / "담당자.md").write_text(
                "Aurora 프로젝트 담당자는 지수입니다.", encoding="utf-8"
            )
            env_file = base / ".env"
            env_file.write_text(
                f'SAVE_YOUR_MEMORY_ROOT="{root}"\n'
                f'SAVE_YOUR_MEMORY_HOME="{home}"\n',
                encoding="utf-8",
            )
            indexed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "save_your_memory",
                    "--env-file",
                    str(env_file),
                    "index",
                    "--json",
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                timeout=30,
            )
            self.assertEqual(indexed.returncode, 0, indexed.stderr)
            queried = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "save_your_memory",
                    "--env-file",
                    str(env_file),
                    "query",
                    "Aurora 담당자",
                    "--json",
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                timeout=30,
            )

            decoded = queried.stdout.decode("utf-8")
            payload = json.loads(decoded)
            self.assertEqual(queried.returncode, 0, queried.stderr)
            self.assertEqual(payload["question"], "Aurora 담당자")
            self.assertIn("지수", payload["results"][0]["excerpt"])

    def test_script_entry_point_runs_from_outside_project_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "source"
            home = base / "memory"
            root.mkdir()
            env_file = base / ".env"
            env_file.write_text(
                f'SAVE_YOUR_MEMORY_ROOT="{root}"\n'
                f'SAVE_YOUR_MEMORY_HOME="{home}"\n',
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts/save_your_memory.py"),
                    "--env-file",
                    str(env_file),
                    "status",
                    "--json",
                ],
                cwd=base,
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=30,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout)["command"], "status")


if __name__ == "__main__":
    unittest.main()
