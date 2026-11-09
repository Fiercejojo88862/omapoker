"""The local music library: folders, tags and playlists.

Tags come from ``mutagen`` when it is installed. Without it the filename is used
instead, so the library still works - it is just less pretty.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

try:  # pragma: no cover - presence depends on the environment
    import mutagen

    MUTAGEN_AVAILABLE = True
except Exception:  # pragma: no cover
    MUTAGEN_AVAILABLE = False

AUDIO_EXTENSIONS = {
    ".mp3",
    ".flac",
    ".m4a",
    ".mp4",
    ".ogg",
    ".oga",
    ".opus",
    ".wav",
    ".aiff",
    ".aif",
    ".wma",
}

PLAYLIST_EXTENSIONS = {".m3u", ".m3u8"}

#: Folders are not walked forever: a deep scan of a huge tree is a bad surprise.
MAX_FILES = 20_000
MAX_DEPTH = 8


@dataclass
class LocalTrack:
    """One audio file, with whatever metadata could be read."""

    path: Path
    title: str = ""
    artist: str = ""
    album: str = ""
    duration: float = 0.0
    track_number: int = 0

    @property
    def label(self) -> str:
        title = self.title or self.path.stem
        return f"{title} - {self.artist}" if self.artist else title

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "duration": self.duration,
            "track_number": self.track_number,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LocalTrack":
        return cls(
            path=Path(data.get("path", "")),
            title=data.get("title", ""),
            artist=data.get("artist", ""),
            album=data.get("album", ""),
            duration=float(data.get("duration", 0) or 0),
            track_number=int(data.get("track_number", 0) or 0),
        )


def _first(tags, key: str) -> str:
    value = tags.get(key)
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return str(value[0]).strip() if value else ""
    return str(value).strip()


def read_tags(path: Path) -> LocalTrack:
    """Read a file's tags, falling back to a clean filename."""
    track = LocalTrack(path=path)
    if not MUTAGEN_AVAILABLE:
        track.title = _clean_stem(path)
        return track
    try:
        audio = mutagen.File(path, easy=True)
    except Exception:
        audio = None
    if audio is None:
        track.title = _clean_stem(path)
        return track

    tags = getattr(audio, "tags", None) or {}
    track.title = _first(tags, "title") or _clean_stem(path)
    track.artist = _first(tags, "artist") or _first(tags, "albumartist")
    track.album = _first(tags, "album")
    number = _first(tags, "tracknumber").split("/")[0]
    track.track_number = int(number) if number.isdigit() else 0

    info = getattr(audio, "info", None)
    length = getattr(info, "length", 0) or 0
    track.duration = float(length)
    return track


def _clean_stem(path: Path) -> str:
    """``03 - Song Name`` becomes ``Song Name``."""
    stem = path.stem.replace("_", " ").strip()
    if " - " in stem:
        head, _, tail = stem.partition(" - ")
        if head.strip().isdigit():
            stem = tail.strip()
    return stem


def is_audio(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTENSIONS


def _walk(folder: Path, depth: int = 0) -> Iterable[Path]:
    if depth > MAX_DEPTH:
        return
    try:
        entries = sorted(os.scandir(folder), key=lambda e: e.name.lower())
    except OSError:
        return
    for entry in entries:
        try:
            if entry.is_dir(follow_symlinks=False):
                if entry.name.startswith("."):
                    continue
                yield from _walk(Path(entry.path), depth + 1)
            elif entry.is_file(follow_symlinks=False):
                yield Path(entry.path)
        except OSError:
            continue


@dataclass
class Library:
    """A set of music folders."""

    folders: list[Path] = field(default_factory=list)
    tracks: list[LocalTrack] = field(default_factory=list)
    scanned: bool = False
    skipped: int = 0

    def __post_init__(self) -> None:
        self.folders = [Path(f).expanduser() for f in self.folders]

    def set_folders(self, folders: Sequence[str | Path]) -> None:
        self.folders = [Path(f).expanduser() for f in folders]
        self.tracks = []
        self.scanned = False

    def scan(self, limit: int = MAX_FILES) -> list[LocalTrack]:
        """Walk the folders and read tags. Safe to call repeatedly."""
        found: list[LocalTrack] = []
        for folder in self.folders:
            if not folder.is_dir():
                continue
            for path in _walk(folder):
                if len(found) >= limit:
                    self.skipped += 1
                    continue
                if is_audio(path):
                    found.append(read_tags(path))
        found.sort(key=lambda t: (t.artist.lower(), t.album.lower(), t.track_number, t.title.lower()))
        self.tracks = found
        self.scanned = True
        return found

    def ensure_scanned(self) -> list[LocalTrack]:
        return self.tracks if self.scanned else self.scan()

    # -- playlists -----------------------------------------------------
    def playlists(self) -> dict[str, list[Path]]:
        """Every ``.m3u`` in the folders, as name -> absolute paths."""
        result: dict[str, list[Path]] = {}
        for folder in self.folders:
            if not folder.is_dir():
                continue
            for path in _walk(folder):
                if path.suffix.lower() in PLAYLIST_EXTENSIONS:
                    entries = self.read_playlist(path)
                    if entries:
                        result[path.stem] = entries
        return result

    @staticmethod
    def read_playlist(path: Path) -> list[Path]:
        """Read a plain ``.m3u`` (comments, blanks and relative paths handled)."""
        entries: list[Path] = []
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return entries
        base = path.parent
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            candidate = Path(line)
            if not candidate.is_absolute():
                candidate = (base / candidate).resolve()
            if candidate.is_file():
                entries.append(candidate)
        return entries

    # -- searching -----------------------------------------------------
    def search(self, term: str, limit: int = 200) -> list[LocalTrack]:
        term = term.strip().lower()
        if not term:
            return self.tracks[:limit]
        hits = [
            track
            for track in self.ensure_scanned()
            if term in track.title.lower()
            or term in track.artist.lower()
            or term in track.album.lower()
        ]
        return hits[:limit]

    def count(self) -> int:
        return len(self.ensure_scanned())

    @staticmethod
    def default_folders() -> list[Path]:
        """Sensible starting folder: ``~/Music``, if it exists."""
        candidate = Path.home() / "Music"
        return [candidate] if candidate.is_dir() else []
