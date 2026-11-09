"""Durable JSON storage.

Every write is atomic and preceded by a backup, because a poker bankroll that
vanishes after a power cut is not a fun surprise. The pattern is the same one
omarchy-lock-studio uses: write to a temporary file in the same directory,
fsync, then ``os.replace`` so a reader never sees a half-written file.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

#: How many timestamped backups to keep per file.
KEEP_BACKUPS = 20


def timestamp() -> str:
    """A timestamp that sorts lexicographically in chronological order.

    Microsecond precision means two backups taken in the same second still order
    correctly, so "the most recent backup" is never a guess.
    """
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def atomic_write_json(path: Path, data: Any, private: bool = True) -> None:
    """Write JSON so that readers only ever see a complete file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    tmp_path = Path(handle.name)
    try:
        with handle:
            json.dump(data, handle, indent=2, sort_keys=False, default=str)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if private:
            os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def read_json(path: Path, default: Any = None) -> Any:
    """Read JSON, returning ``default`` for a missing or corrupt file."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        return default


@dataclass
class JsonStore:
    """A directory of JSON documents, each with its own backup history."""

    root: Path
    backup_dirname: str = "backups"
    keep: int = KEEP_BACKUPS

    def __post_init__(self) -> None:
        self.root = Path(self.root)

    # -- paths ---------------------------------------------------------
    def path(self, name: str) -> Path:
        return self.root / f"{name}.json"

    @property
    def backup_root(self) -> Path:
        return self.root / self.backup_dirname

    # -- operations ----------------------------------------------------
    def exists(self, name: str) -> bool:
        return self.path(name).is_file()

    def load(self, name: str, default: Any = None) -> Any:
        return read_json(self.path(name), default)

    def save(self, name: str, data: Any) -> None:
        target = self.path(name)
        if target.is_file():
            self.backup(name)
        atomic_write_json(target, data)

    def backup(self, name: str) -> Path | None:
        """Copy the current file aside, pruning the oldest beyond ``keep``."""
        source = self.path(name)
        if not source.is_file():
            return None
        self.backup_root.mkdir(parents=True, exist_ok=True)
        destination = self.backup_root / f"{name}.{timestamp()}.json"
        counter = 1
        while destination.exists():
            destination = self.backup_root / f"{name}.{timestamp()}.{counter}.json"
            counter += 1
        try:
            shutil.copy2(source, destination)
        except OSError:
            return None
        self._prune(name)
        return destination

    def _prune(self, name: str) -> None:
        backups = sorted(self.backup_root.glob(f"{name}.*.json"))
        for stale in backups[: max(0, len(backups) - self.keep)]:
            stale.unlink(missing_ok=True)

    def list_names(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.stem for p in self.root.glob("*.json"))

    def delete(self, name: str, keep_backup: bool = True) -> bool:
        target = self.path(name)
        if not target.is_file():
            return False
        if keep_backup:
            self.backup(name)
        target.unlink()
        return True

    def backups_for(self, name: str) -> list[Path]:
        """Backups for a document, most recent first."""
        if not self.backup_root.is_dir():
            return []
        return sorted(self.backup_root.glob(f"{name}.*.json"), reverse=True)

    def restore(self, name: str, backup: Path) -> bool:
        """Put a backup back in place, preserving the current file first."""
        if not backup.is_file():
            return False
        if self.path(name).is_file():
            self.backup(name)
        shutil.copy2(backup, self.path(name))
        return True


class IdentifiedStore(JsonStore):
    """A JsonStore keyed by a slug-safe identifier."""

    @staticmethod
    def slug(text: str) -> str:
        """A filesystem-safe identifier: spaces and oddities become hyphens."""
        keep = []
        for char in text.strip().lower():
            if char.isalnum():
                keep.append(char)
            elif char in " -_":
                keep.append("-")
        slug = "".join(keep)
        while "--" in slug:
            slug = slug.replace("--", "-")
        slug = slug[:48].strip("-")
        return slug or "profile"

    def unique_slug(self, base: str) -> str:
        slug = self.slug(base)
        if not self.exists(slug):
            return slug
        counter = 2
        while self.exists(f"{slug}-{counter}"):
            counter += 1
        return f"{slug}-{counter}"

    def identifiers(self) -> Iterator[str]:
        return iter(self.list_names())
