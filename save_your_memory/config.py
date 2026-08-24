from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class ConfigurationError(ValueError):
    """Raised when the local wiki configuration is invalid."""


DEFAULT_MAX_FILE_BYTES = 25 * 1024 * 1024
DEFAULT_EXCLUDED_DIRECTORIES = (
    ".git",
    ".hg",
    ".svn",
    ".omx",
    "__pycache__",
    "node_modules",
)


def _parse_dotenv(path: Path | None) -> dict[str, str]:
    if path is None or not path.exists():
        return {}
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ConfigurationError(
                f"Invalid .env entry at {path}:{line_number}: expected KEY=VALUE"
            )
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def _resolve_path(value: str, base: Path) -> Path:
    candidate = Path(os.path.expandvars(os.path.expanduser(value)))
    if not candidate.is_absolute():
        candidate = base / candidate
    return candidate.resolve(strict=False)


@dataclass(frozen=True)
class Settings:
    root: Path
    home: Path
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    excluded_directories: tuple[str, ...] = DEFAULT_EXCLUDED_DIRECTORIES

    @classmethod
    def load(
        cls,
        *,
        env: Mapping[str, str] | None = None,
        env_file: Path | None = None,
        cwd: Path | None = None,
    ) -> "Settings":
        process_values = dict(os.environ if env is None else env)
        file_values = _parse_dotenv(env_file)
        values = {**file_values, **process_values}
        base = (env_file.parent if env_file is not None else (cwd or Path.cwd())).resolve()

        root_value = values.get("SAVE_YOUR_MEMORY_ROOT", "").strip()
        if not root_value:
            raise ConfigurationError(
                "SAVE_YOUR_MEMORY_ROOT is required; set it to the parent directory to index"
            )
        root = _resolve_path(root_value, base)
        if not root.exists():
            raise ConfigurationError(f"Source root does not exist: {root}")
        if not root.is_dir():
            raise ConfigurationError(f"Source root is not a directory: {root}")

        home_value = values.get("SAVE_YOUR_MEMORY_HOME", "").strip()
        if home_value:
            home = _resolve_path(home_value, base)
        else:
            local_app_data = values.get("LOCALAPPDATA", "").strip()
            home_base = Path(local_app_data) if local_app_data else Path.home() / ".local" / "share"
            home = (home_base / "save-your-memory").resolve(strict=False)
        if home == root:
            raise ConfigurationError(
                "SAVE_YOUR_MEMORY_HOME must differ from SAVE_YOUR_MEMORY_ROOT"
            )

        raw_max_bytes = values.get(
            "SAVE_YOUR_MEMORY_MAX_FILE_BYTES", str(DEFAULT_MAX_FILE_BYTES)
        )
        try:
            max_file_bytes = int(raw_max_bytes)
        except ValueError as exc:
            raise ConfigurationError(
                "SAVE_YOUR_MEMORY_MAX_FILE_BYTES must be an integer"
            ) from exc
        if max_file_bytes <= 0:
            raise ConfigurationError(
                "SAVE_YOUR_MEMORY_MAX_FILE_BYTES must be greater than zero"
            )

        return cls(root=root, home=home, max_file_bytes=max_file_bytes)
