from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .config import Settings


@dataclass(frozen=True)
class ScanEntry:
    absolute_path: Path
    relative_path: Path
    kind: str
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class ScanResult:
    entries: tuple[ScanEntry, ...]
    errors: tuple[str, ...]


def _is_reparse_point(file_stat: os.stat_result) -> bool:
    attributes = getattr(file_stat, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(reparse_flag and attributes & reparse_flag)


def scan_tree(settings: Settings) -> ScanResult:
    entries: list[ScanEntry] = []
    errors: list[str] = []
    excluded_names = {name.casefold() for name in settings.excluded_directories}
    root = settings.root
    home = settings.home

    def visit(directory: Path) -> None:
        try:
            with os.scandir(directory) as iterator:
                children = sorted(iterator, key=lambda item: item.name.casefold())
        except OSError as exc:
            errors.append(f"{directory}: {exc}")
            return

        for child in children:
            path = Path(child.path)
            relative = path.relative_to(root)
            try:
                child_stat = child.stat(follow_symlinks=False)
            except OSError as exc:
                errors.append(f"{path}: {exc}")
                continue
            if child.is_symlink() or _is_reparse_point(child_stat):
                continue
            try:
                if child.is_dir(follow_symlinks=False):
                    if child.name.casefold() in excluded_names:
                        continue
                    if path.resolve(strict=False) == home:
                        continue
                    entries.append(
                        ScanEntry(
                            absolute_path=path,
                            relative_path=relative,
                            kind="directory",
                            size=0,
                            mtime_ns=child_stat.st_mtime_ns,
                        )
                    )
                    visit(path)
                elif child.is_file(follow_symlinks=False):
                    entries.append(
                        ScanEntry(
                            absolute_path=path,
                            relative_path=relative,
                            kind="file",
                            size=child_stat.st_size,
                            mtime_ns=child_stat.st_mtime_ns,
                        )
                    )
            except OSError as exc:
                errors.append(f"{path}: {exc}")

    visit(root)
    entries.sort(key=lambda entry: entry.relative_path.as_posix().casefold())
    return ScanResult(tuple(entries), tuple(errors))
