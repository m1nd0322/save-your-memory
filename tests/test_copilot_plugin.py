import json
import re
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

from save_your_memory import __version__


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"


class CopilotAgentPluginTests(unittest.TestCase):
    def test_agent_plugin_manifest_exposes_matching_portable_skill(self) -> None:
        manifest = json.loads((PROJECT_ROOT / "plugin.json").read_text(encoding="utf-8"))
        codex_manifest = json.loads(
            (PROJECT_ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
        )
        project = tomllib.loads(
            (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        skill_path = PROJECT_ROOT / "skills/save-your-memory/SKILL.md"
        skill_text = skill_path.read_text(encoding="utf-8")
        skill_name = re.search(r"^name:\s*([^\s]+)$", skill_text, flags=re.MULTILINE)

        self.assertEqual(manifest["$schema"], AGENT_PLUGIN_SCHEMA)
        self.assertEqual(manifest["name"], "save-your-memory")
        self.assertEqual(manifest["version"], __version__)
        self.assertEqual(codex_manifest["version"], __version__)
        self.assertEqual(project["project"]["version"], __version__)
        self.assertTrue(skill_path.is_file())
        self.assertIsNotNone(skill_name)
        self.assertEqual(skill_name.group(1), skill_path.parent.name)
        expected_command = f"/{manifest['name']}:{skill_name.group(1)}"
        guide = (PROJECT_ROOT / "docs/vscode-copilot.md").read_text(encoding="utf-8")
        self.assertEqual(expected_command, "/save-your-memory:save-your-memory")
        self.assertIn(expected_command, guide)
        self.assertIn("https://github.com/m1nd0322/save-your-memory", guide)

    def test_vscode_workspace_enables_plugins_without_publishing_local_paths(self) -> None:
        settings = json.loads(
            (PROJECT_ROOT / ".vscode/settings.json").read_text(encoding="utf-8")
        )
        workspace = json.loads(
            (PROJECT_ROOT / "save-your-memory.code-workspace").read_text(
                encoding="utf-8"
            )
        )
        self.assertIs(settings["chat.plugins.enabled"], True)
        self.assertNotIn("chat.pluginLocations", settings)
        self.assertEqual(workspace["folders"], [{"path": "."}])
        self.assertIs(workspace["settings"]["chat.plugins.enabled"], True)
        self.assertNotIn("chat.pluginLocations", workspace["settings"])

    def test_portable_skill_resolves_and_runs_bundled_cli(self) -> None:
        skill_path = PROJECT_ROOT / "skills/save-your-memory/SKILL.md"
        plugin_root = skill_path.parents[2]
        script_path = plugin_root / "scripts/save_your_memory.py"

        completed = subprocess.run(
            [sys.executable, str(script_path), "--help"],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=30,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("index", completed.stdout)
        self.assertIn("query", completed.stdout)
        self.assertIn("status", completed.stdout)
        self.assertIn("lint", completed.stdout)


if __name__ == "__main__":
    unittest.main()
