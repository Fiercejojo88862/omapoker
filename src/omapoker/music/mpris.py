"""MPRIS control through ``playerctl``.

OmaPoker cannot stream Apple Music or Spotify itself - they are DRM'd. What it
*can* do is drive whatever player is already running on your system, including a
browser tab playing Apple Music in Firefox. That is what this backend does.

Every call is best-effort: a missing player, a hung player or a missing
``playerctl`` yields ``None`` or ``False``, never an exception, because a music
problem must never take the poker table down with it.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

#: Go-template fields playerctl 2.4 understands, tab separated to survive titles
#: that contain pipes or dashes.
_METADATA_FORMAT = "\t".join(
    [
        "{{playerName}}",
        "{{status}}",
        "{{title}}",
        "{{artist}}",
        "{{album}}",
        "{{mpris:length}}",
    ]
)


@dataclass
class Track:
    """One playing thing, from any MPRIS player."""

    player: str = ""
    status: str = "Stopped"
    title: str = ""
    artist: str = ""
    album: str = ""
    length_us: int = 0

    @property
    def is_playing(self) -> bool:
        return self.status.lower() == "playing"

    @property
    def label(self) -> str:
        """``Title - Artist``, or just whichever part exists."""
        title = self.title.strip() or "(nothing playing)"
        artist = self.artist.strip()
        return f"{title} - {artist}" if artist else title

    @property
    def duration_seconds(self) -> int:
        return max(0, int(self.length_us // 1_000_000)) if self.length_us else 0

    def short(self, width: int = 34) -> str:
        """The label trimmed to fit a header chip."""
        return _ellipsis(self.label, width)


def _ellipsis(text: str, width: int) -> str:
    text = " ".join(text.split())
    if width <= 1 or len(text) <= width:
        return text
    return text[: width - 1].rstrip() + "\u2026"


def _to_int(text: str) -> int:
    try:
        return int(float(text.strip()))
    except (TypeError, ValueError):
        return 0


class MprisBackend:
    """A thin, forgiving wrapper around the ``playerctl`` command."""

    def __init__(self, binary: str = "playerctl", timeout: float = 2.5) -> None:
        self.binary = binary
        self.timeout = timeout

    # -- plumbing ------------------------------------------------------
    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def _run(self, *args: str) -> tuple[int, str]:
        if not self.available():
            return 1, ""
        try:
            completed = subprocess.run(
                [self.binary, *args],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return 1, ""
        return completed.returncode, completed.stdout.strip()

    # -- discovery -----------------------------------------------------
    def players(self) -> list[str]:
        code, out = self._run("-l")
        if code != 0 or not out:
            return []
        names = [line.strip() for line in out.splitlines() if line.strip()]
        # playerctld is a proxy, not a real player: it would double every entry.
        return [name for name in names if name != "playerctld"]

    def track(self, player: str | None = None) -> Track | None:
        """Metadata for one player, or for the best available player."""
        args = ["-p", player] if player else []
        code, out = self._run(*args, "metadata", "--format", _METADATA_FORMAT)
        if code != 0 or not out:
            return None
        fields = out.split("\t")
        while len(fields) < 6:
            fields.append("")
        track = Track(
            player=fields[0].strip() or (player or ""),
            status=fields[1].strip(),
            title=fields[2].strip(),
            artist=fields[3].strip(),
            album=fields[4].strip(),
            length_us=_to_int(fields[5]),
        )
        if not player:
            # Without -p, playerctl picks one; ask it which, per status.
            track.player = track.player or ""
        return track

    def best_track(self, preferred: str | None = None) -> Track | None:
        """The track a user would expect the chip to show."""
        if preferred:
            track = self.track(preferred)
            if track and (track.title or track.is_playing):
                return track
        candidates = []
        for player in self.players():
            track = self.track(player)
            if track and (track.title or track.is_playing):
                candidates.append(track)
        if not candidates:
            return None
        playing = [t for t in candidates if t.is_playing]
        return (playing or candidates)[0]

    # -- control -------------------------------------------------------
    def status(self, player: str | None = None) -> str:
        args = ["-p", player] if player else []
        _code, out = self._run(*args, "status")
        return out.strip() or "Stopped"

    def _do(self, verb: str, player: str | None = None) -> bool:
        args = ["-p", player] if player else []
        code, _ = self._run(*args, verb)
        return code == 0

    def play_pause(self, player: str | None = None) -> bool:
        return self._do("play-pause", player)

    def play(self, player: str | None = None) -> bool:
        return self._do("play", player)

    def pause(self, player: str | None = None) -> bool:
        return self._do("pause", player)

    def next_track(self, player: str | None = None) -> bool:
        return self._do("next", player)

    def previous_track(self, player: str | None = None) -> bool:
        return self._do("previous", player)

    def stop(self, player: str | None = None) -> bool:
        return self._do("stop", player)

    def set_volume(self, level: float, player: str | None = None) -> bool:
        level = max(0.0, min(1.0, level))
        args = ["-p", player] if player else []
        code, _ = self._run(*args, "volume", f"{level:.2f}")
        return code == 0

    def volume(self, player: str | None = None) -> float | None:
        args = ["-p", player] if player else []
        code, out = self._run(*args, "volume")
        if code != 0 or not out:
            return None
        try:
            return float(out.split()[-1])
        except (ValueError, IndexError):
            return None


def describe_source(source: str, track: Track | None = None) -> str:
    """A friendly name for a source id, e.g. ``firefox`` -> ``Firefox``."""
    if source == "off":
        return "Off"
    if source == "local":
        return "OmaPoker Local"
    if source.startswith("mpris:"):
        player = source.split(":", 1)[1]
        if track and track.title and track.player == player:
            return f"{player.capitalize()} - {track.title}"
        return player.capitalize()
    return source
