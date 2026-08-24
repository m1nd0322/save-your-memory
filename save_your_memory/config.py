from __future__ import annotations

import os
import re
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
    ".venv",
    "build",
    "dist",
    "download",
    "downloads",
    "env",
    "node_modules",
    "site-packages",
    "third-party",
    "third_party",
    "vendor",
    "venv",
)
DEFAULT_EXCLUDED_EXTENSIONS = (".csv",)
DEFAULT_EXCLUDED_DIRECTORY_GLOBS = ("*.git",)
DEFAULT_CHUNK_BYTES = 32 * 1024
_SETTING_SEPARATOR = re.compile(r"[,\n;]+")


def _split_setting_values(value: str) -> tuple[str, ...]:
    items: list[str] = []
    for raw_item in _SETTING_SEPARATOR.split(value):
        item = raw_item.strip()
        if item:
            items.append(item)
    return tuple(items)


def _merge_unique(
    defaults: tuple[str, ...],
    extras: tuple[str, ...],
    *,
    key,
) -> tuple[str, ...]:
    values: list[str] = []
    seen: set[str] = set()
    for item in (*defaults, *extras):
        marker = key(item)
        if marker in seen:
            continue
        seen.add(marker)
        values.append(item)
    return tuple(values)


def _normalize_extension(value: str) -> str:
    item = value.strip().casefold()
    if not item:
        return ""
    return item if item.startswith(".") else f".{item}"


def _normalize_directory(value: str) -> str:
    return value.strip()


def _parse_positive_int(value: str, *, field_name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigurationError(f"{field_name} must be an integer") from exc
    if parsed <= 0:
        raise ConfigurationError(f"{field_name} must be greater than zero")
    return parsed


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
    excluded_directory_globs: tuple[str, ...] = DEFAULT_EXCLUDED_DIRECTORY_GLOBS
    excluded_extensions: tuple[str, ...] = DEFAULT_EXCLUDED_EXTENSIONS
    chunk_bytes: int = DEFAULT_CHUNK_BYTES

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
        max_file_bytes = _parse_positive_int(
            raw_max_bytes, field_name="SAVE_YOUR_MEMORY_MAX_FILE_BYTES"
        )

        excluded_directories = _merge_unique(
            DEFAULT_EXCLUDED_DIRECTORIES,
            tuple(
                _normalize_directory(item)
                for item in _split_setting_values(
                    values.get("SAVE_YOUR_MEMORY_EXCLUDED_DIRECTORIES", "")
                )
            ),
            key=str.casefold,
        )
        excluded_extensions = _merge_unique(
            DEFAULT_EXCLUDED_EXTENSIONS,
            tuple(
                normalized
                for normalized in (
                    _normalize_extension(item)
                    for item in _split_setting_values(
                        values.get("SAVE_YOUR_MEMORY_EXCLUDED_EXTENSIONS", "")
                    )
                )
                if normalized
            ),
            key=str.casefold,
        )
        excluded_directory_globs = _merge_unique(
            DEFAULT_EXCLUDED_DIRECTORY_GLOBS,
            _split_setting_values(
                values.get("SAVE_YOUR_MEMORY_EXCLUDED_DIRECTORY_GLOBS", "")
            ),
            key=str.casefold,
        )

        chunk_bytes = _parse_positive_int(
            values.get("SAVE_YOUR_MEMORY_CHUNK_BYTES", str(DEFAULT_CHUNK_BYTES)),
            field_name="SAVE_YOUR_MEMORY_CHUNK_BYTES",
        )
        return cls(
            root=root,
            home=home,
            max_file_bytes=max_file_bytes,
            excluded_directories=excluded_directories,
            excluded_directory_globs=excluded_directory_globs,
            excluded_extensions=excluded_extensions,
            chunk_bytes=chunk_bytes,
        )
