"""Music: MPRIS control for your own players, plus a local mpv library.

The header chip cycles through the sources available *right now*:

    Off  ->  Firefox (Apple Music)  ->  Spotify  ->  ...  ->  OmaPoker Local

Rules that keep it polite:

* Switching away from a player **pauses** it, but only if OmaPoker was the thing
  that started it playing. A player you started yourself is left alone.
* ``Off`` only ever pauses what OmaPoker controls. It never kills a player you
  are managing.
* Every failure is swallowed. A missing ``playerctl`` or a broken audio device
  costs you the equaliser, nothing else.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .. import paths
from .cava import DEFAULT_BARS, Visualiser
from .library import Library, LocalTrack
from .local import LocalPlayer, LocalStatus, socket_path_for
from .mpris import MprisBackend, Track, describe_source

OFF = "off"
LOCAL = "local"
MPRIS_PREFIX = "mpris:"

#: Friendly names for the common browser players, since a player id alone
#: ("firefox.instance_1_72") is not much help in a header chip.
SOURCE_HINTS = {
    "firefox": "Firefox (Apple Music / web)",
    "chromium": "Chromium (web player)",
    "chrome": "Chrome (web player)",
    "google-chrome": "Chrome (web player)",
    "spotify": "Spotify",
    "vlc": "VLC",
    "mpv": "mpv",
    "playerctld": "playerctld",
}


def _hint_for(player: str) -> str:
    base = player.split(".", 1)[0].lower()
    return SOURCE_HINTS.get(base, player.capitalize())


@dataclass
class MusicState:
    """A snapshot for the UI."""

    source: str = OFF
    source_label: str = "Off"
    title: str = ""
    artist: str = ""
    playing: bool = False
    detail: str = ""
    position: float = 0.0
    duration: float = 0.0
    queue_position: int = 0
    queue_length: int = 0
    shuffle: bool = False

    @property
    def label(self) -> str:
        if not self.title:
            return "(nothing playing)"
        return f"{self.title} - {self.artist}" if self.artist else self.title

    def short(self, width: int = 36) -> str:
        text = " ".join(self.label.split())
        if len(text) <= width:
            return text
        return text[: max(1, width - 1)].rstrip() + "\u2026"


class MusicController:
    """One object the UI talks to for everything musical."""

    def __init__(self, settings: dict | None = None, state_dir: Path | str | None = None) -> None:
        settings = dict(settings or {})
        self.state_dir = Path(state_dir) if state_dir else paths.STATE_DIR
        self.volume = int(settings.get("music_volume", 60))
        self.resume_local = bool(settings.get("music_resume", False))
        self.shuffle = bool(settings.get("music_shuffle", False))
        self.repeat = bool(settings.get("music_repeat", False))
        self.source = str(settings.get("music_source", OFF) or OFF)
        self.bars = int(settings.get("visualiser_bars", DEFAULT_BARS) or DEFAULT_BARS)
        self.visualiser_enabled = bool(settings.get("visualiser", True))

        folders = settings.get("music_folders") or []
        self.mpris = MprisBackend()
        self.library = Library(folders or Library.default_folders())
        self.player = LocalPlayer(
            socket_path=socket_path_for(self.state_dir),
            volume=self.volume,
            shuffle=self.shuffle,
            repeat_file=self.repeat,
        )
        self.visualiser: Visualiser | None = None
        if self.visualiser_enabled:
            self.visualiser = Visualiser(
                bars=self.bars,
                state_dir=self.state_dir / "cava",
                input_method=str(settings.get("cava_input", "pulse")),
                source=str(settings.get("cava_source", "auto")),
            )

        self._track: Track | None = None
        self._local = LocalStatus()
        self._we_started: set[str] = set()
        self._local_was_playing = False
        self._phase = 0.0

    # ------------------------------------------------------------------
    # sources
    # ------------------------------------------------------------------
    def mpris_players(self) -> list[str]:
        return self.mpris.players()

    def available_sources(self) -> list[str]:
        sources = [OFF]
        sources.extend(f"{MPRIS_PREFIX}{player}" for player in self.mpris_players())
        sources.append(LOCAL)
        return sources

    def source_label(self, source: str) -> str:
        if source == OFF:
            return "Off"
        if source == LOCAL:
            return "OmaPoker Local"
        if source.startswith(MPRIS_PREFIX):
            return _hint_for(source[len(MPRIS_PREFIX) :])
        return source

    def source_labels(self) -> list[tuple[str, str]]:
        """``[(id, label)]`` for a picker."""
        return [(source, self.source_label(source)) for source in self.available_sources()]

    def cycle_source(self, step: int = 1) -> str:
        """Move to the next source, wrapping around."""
        sources = self.available_sources()
        if not sources:
            return self.source
        if self.source not in sources:
            self.set_source(sources[0])
            return self.source
        index = (sources.index(self.source) + step) % len(sources)
        self.set_source(sources[index])
        return self.source

    def set_source(self, source: str) -> str:
        """Switch source, pausing whatever OmaPoker started on the old one."""
        if source == self.source:
            self.refresh()
            return self.source

        # Pause the old source, but respectfully.
        if self.source.startswith(MPRIS_PREFIX):
            player = self.source[len(MPRIS_PREFIX) :]
            if player in self._we_started:
                self.mpris.pause(player)
                self._we_started.discard(player)
        elif self.source == LOCAL:
            status = self.player.status() if self.player.is_running() else LocalStatus()
            self._local_was_playing = status.is_playing
            if status.is_playing:
                self.player.set_pause(True)

        self.source = source

        # Wake the new source up.
        if source == LOCAL:
            self.player.start()
            if self._local_was_playing or self.resume_local:
                self.player.set_pause(False)
        elif source.startswith(MPRIS_PREFIX):
            player = source[len(MPRIS_PREFIX) :]
            if self.mpris.status(player).lower() == "playing":
                self._we_started.add(player)
        self.refresh()
        return self.source

    # ------------------------------------------------------------------
    # state
    # ------------------------------------------------------------------
    def refresh(self) -> MusicState:
        """Poll the current source. Cheap enough to call on a timer."""
        self._track = None
        self._local = LocalStatus()

        if self.source.startswith(MPRIS_PREFIX):
            player = self.source[len(MPRIS_PREFIX) :]
            track = self.mpris.track(player)
            if track is None:
                # The player went away (the browser tab closed, say): fall back
                # to Off rather than showing a stale title forever.
                self.source = OFF
            else:
                self._track = track
        return self.state()

    def state(self) -> MusicState:
        state = MusicState(source=self.source, source_label=self.source_label(self.source))
        if self.source.startswith(MPRIS_PREFIX) and self._track is not None:
            state.title = self._track.title
            state.artist = self._track.artist
            state.playing = self._track.is_playing
            state.duration = float(self._track.duration_seconds)
            state.detail = self._track.album
        elif self.source == LOCAL:
            status = self._local
            if self.player.is_running():
                status = self.player.status()
                self._local = status
            filename = status.filename
            state.title = status.title or (Path(filename).stem if filename else "")
            state.playing = status.is_playing
            state.position = status.position
            state.duration = status.duration
            state.queue_position = max(0, status.playlist_pos + 1)
            state.queue_length = status.playlist_count
            state.shuffle = status.shuffle
            state.detail = (
                f"track {state.queue_position}/{state.queue_length}"
                if state.queue_length
                else ""
            )
        return state

    def now_playing(self) -> str:
        state = self.state()
        if state.source == OFF:
            return "Off"
        return state.label

    def is_playing(self) -> bool:
        return self.state().playing

    def chip_text(self, width: int = 36) -> str:
        state = self.state()
        if state.source == OFF:
            return "Off"
        if not state.title:
            return f"{state.source_label} - idle"
        mark = "\u25b6" if state.playing else "\u23f8"
        return f"{state.short(width)} {mark}"

    def levels(self, count: int = DEFAULT_BARS) -> tuple[list[float], bool]:
        """``(levels, live)`` - live is False when nothing real is moving."""
        if self.visualiser is None:
            return ([0.0] * count, False)
        self.visualiser.ensure_running()
        if not self.visualiser.alive():
            return ([0.0] * count, False)
        data = self.visualiser.levels(count)
        live = any(value > 0.01 for value in data)
        return (data, live)

    # ------------------------------------------------------------------
    # controls
    # ------------------------------------------------------------------
    def _current_player(self) -> str | None:
        if self.source.startswith(MPRIS_PREFIX):
            return self.source[len(MPRIS_PREFIX) :]
        return None

    def toggle_play(self) -> bool:
        player = self._current_player()
        if player:
            ok = self.mpris.play_pause(player)
            track = self.mpris.track(player)
            if track and track.is_playing:
                self._we_started.add(player)
            else:
                self._we_started.discard(player)
            self.refresh()
            return ok
        if self.source == LOCAL:
            ok = self.player.toggle_pause()
            self.refresh()
            return ok
        return False

    def play(self) -> bool:
        player = self._current_player()
        if player:
            ok = self.mpris.play(player)
            if ok:
                self._we_started.add(player)
            self.refresh()
            return ok
        if self.source == LOCAL:
            ok = self.player.set_pause(False)
            self.refresh()
            return ok
        return False

    def pause(self) -> bool:
        player = self._current_player()
        if player:
            ok = self.mpris.pause(player)
            self._we_started.discard(player)
            self.refresh()
            return ok
        if self.source == LOCAL:
            ok = self.player.set_pause(True)
            self.refresh()
            return ok
        return False

    def next_track(self) -> bool:
        player = self._current_player()
        if player:
            ok = self.mpris.next_track(player)
            self.refresh()
            return ok
        if self.source == LOCAL:
            ok = self.player.next_track()
            self.refresh()
            return ok
        return False

    def previous_track(self) -> bool:
        player = self._current_player()
        if player:
            ok = self.mpris.previous_track(player)
            self.refresh()
            return ok
        if self.source == LOCAL:
            ok = self.player.previous_track()
            self.refresh()
            return ok
        return False

    def set_volume(self, level: int) -> int:
        self.volume = max(0, min(100, int(level)))
        player = self._current_player()
        if player:
            self.mpris.set_volume(self.volume / 100, player)
        self.player.set_volume(self.volume)
        return self.volume

    def adjust_volume(self, delta: int) -> int:
        return self.set_volume(self.volume + delta)

    def set_shuffle(self, enabled: bool) -> bool:
        self.shuffle = bool(enabled)
        if self.source == LOCAL:
            if enabled:
                self.player.set_shuffle(True)
                self.player.shuffle_queue()
            else:
                self.player.set_shuffle(False)
        return self.shuffle

    def set_repeat(self, enabled: bool) -> bool:
        self.repeat = bool(enabled)
        self.player.set_repeat_file(self.repeat)
        return self.repeat

    # ------------------------------------------------------------------
    # local library
    # ------------------------------------------------------------------
    def rescan(self) -> int:
        self.library.scan()
        return self.library.count()

    def play_local(self, tracks: Sequence[LocalTrack | str | Path]) -> bool:
        paths = [t.path if isinstance(t, LocalTrack) else Path(t) for t in tracks]
        if not paths:
            return False
        self.set_source(LOCAL)
        return self.player.play(paths, replace=True)

    def enqueue_local(self, tracks: Sequence[LocalTrack | str | Path]) -> bool:
        paths = [t.path if isinstance(t, LocalTrack) else Path(t) for t in tracks]
        if not paths:
            return False
        return self.player.enqueue(paths)

    # ------------------------------------------------------------------
    # housekeeping
    # ------------------------------------------------------------------
    def settings_updates(self) -> dict:
        """The settings this controller owns, ready to persist."""
        return {
            "music_volume": self.volume,
            "music_source": self.source,
            "music_folders": [str(f) for f in self.library.folders],
            "music_resume": self.resume_local,
            "music_shuffle": self.shuffle,
            "music_repeat": self.repeat,
            "visualiser": self.visualiser_enabled,
            "visualiser_bars": self.bars,
        }

    def status_line(self) -> str:
        state = self.state()
        if state.source == OFF:
            return "Music off. Press M for the music panel."
        lines = [f"Source: {state.source_label}"]
        if state.title:
            lines.append(f"Playing: {state.label}")
        if state.detail:
            lines.append(state.detail)
        if self.visualiser is not None:
            lines.append(self.visualiser.summary())
        if self.source == LOCAL and not self.player.available():
            lines.append("mpv is not installed, so local playback is unavailable.")
        return "\n".join(lines)

    def shutdown(self) -> None:
        """Stop only what we own: the equaliser and our own mpv."""
        if self.visualiser is not None:
            self.visualiser.stop()
        if self.player.is_running():
            self.player.quit()


def idle_wave(count: int, phase: float) -> list[float]:
    """A calm idle animation for a dead visualiser."""
    return [((math.sin(phase + i * 0.7) + 1) / 2) * 0.16 for i in range(max(0, count))]


__all__ = [
    "LOCAL",
    "MPRIS_PREFIX",
    "OFF",
    "Library",
    "LocalPlayer",
    "LocalStatus",
    "LocalTrack",
    "MusicController",
    "MusicState",
    "MprisBackend",
    "Visualiser",
    "describe_source",
    "idle_wave",
]
