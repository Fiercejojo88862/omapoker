"""Local playback through our own ``mpv`` instance.

OmaPoker spawns a dedicated mpv process with a private IPC socket rather than
touching whatever player the user already has open. Because ``mpv-mpris`` is
installed on Omarchy, that instance also appears as a system MPRIS player, so
the bar and the media keys pick it up too.

Everything degrades quietly: if mpv is missing or the socket cannot be reached,
playback simply reports failure and the poker table carries on.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

#: A unix socket path must stay under ~108 bytes, so long home directories get
#: a socket in the temporary directory instead.
SOCKET_LIMIT = 100

#: How long to wait for a reply from mpv before giving up on it.
IPC_TIMEOUT = 1.2


def socket_path_for(directory: Path) -> Path:
    candidate = Path(directory) / "mpv.sock"
    if len(str(candidate)) < SOCKET_LIMIT:
        return candidate
    return Path(tempfile.gettempdir()) / f"omapoker-mpv-{os.getuid()}.sock"


@dataclass
class LocalStatus:
    """What mpv is doing right now."""

    filename: str = ""
    title: str = ""
    paused: bool = True
    volume: float = 60.0
    position: float = 0.0
    duration: float = 0.0
    playlist_pos: int = -1
    playlist_count: int = 0
    shuffle: bool = False
    repeat_file: bool = False
    idle: bool = True

    @property
    def is_playing(self) -> bool:
        return bool(self.filename) and not self.paused

    @property
    def progress(self) -> float:
        if self.duration <= 0:
            return 0.0
        return max(0.0, min(1.0, self.position / self.duration))

    @property
    def label(self) -> str:
        if not self.filename:
            return "(nothing queued)"
        return Path(self.filename).stem if not self.title else self.title


class LocalPlayer:
    """Controls a private ``mpv`` process over its JSON IPC socket."""

    def __init__(
        self,
        socket_path: Path | str | None = None,
        *,
        binary: str = "mpv",
        volume: float = 60,
        shuffle: bool = False,
        repeat_file: bool = False,
    ) -> None:
        self.binary = binary
        self.socket_path = Path(socket_path) if socket_path else socket_path_for(Path(tempfile.gettempdir()))
        self.volume = max(0.0, min(100.0, float(volume)))
        self.shuffle = shuffle
        self.repeat_file = repeat_file
        self._process: subprocess.Popen | None = None
        self._request_id = 0

    # -- lifecycle -----------------------------------------------------
    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def is_running(self) -> bool:
        if self._process is not None and self._process.poll() is None:
            return True
        return self.socket_path.exists() and self._ping()

    def start(self) -> bool:
        """Spawn mpv if it is not already up."""
        if not self.available():
            return False
        if self.is_running():
            return True
        try:
            self.socket_path.unlink(missing_ok=True)
        except OSError:
            pass
        args = [
            self.binary,
            "--idle=yes",
            "--no-video",
            "--no-terminal",
            "--really-quiet",
            "--audio-display=no",
            "--force-window=no",
            f"--input-ipc-server={self.socket_path}",
            f"--volume={self.volume:.0f}",
        ]
        if self.shuffle:
            args.append("--shuffle")
        if self.repeat_file:
            args.append("--loop-file=inf")
        try:
            self._process = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            self._process = None
            return False
        # Wait for the socket to appear, but never for long.
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if self.socket_path.exists() and self._ping():
                return True
            time.sleep(0.05)
        return False

    def quit(self) -> None:
        """Ask mpv to exit, then make sure it really did."""
        self.command("quit")
        process = self._process
        if process is not None:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
        self._process = None
        try:
            self.socket_path.unlink(missing_ok=True)
        except OSError:
            pass

    # -- IPC -----------------------------------------------------------
    def _connect(self) -> socket.socket | None:
        if not self.socket_path.exists():
            return None
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(IPC_TIMEOUT)
        try:
            client.connect(str(self.socket_path))
        except OSError:
            client.close()
            return None
        return client

    def _read_reply(self, client: socket.socket, request_id: int) -> dict | None:
        """Read until the reply for ``request_id`` arrives, ignoring events."""
        deadline = time.monotonic() + IPC_TIMEOUT
        buffer = b""
        while time.monotonic() < deadline:
            try:
                chunk = client.recv(65536)
            except (socket.timeout, OSError):
                return None
            if not chunk:
                return None
            buffer += chunk
            while b"\n" in buffer:
                line, _, buffer = buffer.partition(b"\n")
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if message.get("request_id") == request_id:
                    return message
        return None

    def _ping(self) -> bool:
        return self.command("get_property", "idle") is not None

    def command(self, *args) -> dict | None:
        """Send one IPC command and return its reply."""
        replies = self.commands([list(args)])
        return replies[0] if replies else None

    def commands(self, batch: Sequence[list]) -> list[dict | None]:
        """Send several commands over one connection (useful for polling)."""
        client = self._connect()
        if client is None:
            return [None] * len(batch)
        replies: list[dict | None] = []
        try:
            for command in batch:
                self._request_id += 1
                request_id = self._request_id
                payload = json.dumps({"command": list(command), "request_id": request_id})
                try:
                    client.sendall(payload.encode("utf-8") + b"\n")
                except OSError:
                    replies.append(None)
                    continue
                replies.append(self._read_reply(client, request_id))
        finally:
            client.close()
        return replies

    # -- queries -------------------------------------------------------
    def properties(self, names: Sequence[str]) -> dict[str, object]:
        """Read several properties in one round trip."""
        replies = self.commands([["get_property", name] for name in names])
        result: dict[str, object] = {}
        for name, reply in zip(names, replies):
            if reply is None:
                continue
            if reply.get("error") and reply["error"] != "success":
                continue
            if "data" in reply:
                result[name] = reply["data"]
        return result

    def status(self) -> LocalStatus:
        names = [
            "filename",
            "media-title",
            "pause",
            "volume",
            "time-pos",
            "duration",
            "playlist-pos",
            "playlist-count",
            "shuffle",
            "loop-file",
            "idle-active",
        ]
        values = self.properties(names)
        status = LocalStatus()
        status.filename = str(values.get("filename") or "")
        status.title = str(values.get("media-title") or "")
        status.paused = bool(values.get("pause", True))
        try:
            status.volume = float(values.get("volume", self.volume) or 0)
        except (TypeError, ValueError):
            status.volume = self.volume
        try:
            status.position = float(values.get("time-pos") or 0)
        except (TypeError, ValueError):
            status.position = 0.0
        try:
            status.duration = float(values.get("duration") or 0)
        except (TypeError, ValueError):
            status.duration = 0.0
        try:
            status.playlist_pos = int(values.get("playlist-pos", -1))
        except (TypeError, ValueError):
            status.playlist_pos = -1
        try:
            status.playlist_count = int(values.get("playlist-count", 0))
        except (TypeError, ValueError):
            status.playlist_count = 0
        status.shuffle = values.get("shuffle") == "yes"
        status.repeat_file = str(values.get("loop-file") or "no") not in ("no", "false", "0")
        status.idle = bool(values.get("idle-active", False))
        return status

    # -- playback ------------------------------------------------------
    def play(self, paths: Sequence[str | Path], replace: bool = True) -> bool:
        """Load one or more files. ``replace`` clears the queue first."""
        if not paths:
            return False
        if not self.start():
            return False
        mode = "replace" if replace else "append"
        first, *rest = [str(p) for p in paths]
        if not self.command("loadfile", first, mode):
            return False
        for path in rest:
            self.command("loadfile", path, "append")
        return True

    def enqueue(self, paths: Sequence[str | Path]) -> bool:
        return self.play(paths, replace=False)

    def toggle_pause(self) -> bool:
        return bool(self.command("cycle", "pause"))

    def set_pause(self, paused: bool) -> bool:
        return bool(self.command("set_property", "pause", "yes" if paused else "no"))

    def next_track(self) -> bool:
        return bool(self.command("playlist-next", "weak"))

    def previous_track(self) -> bool:
        return bool(self.command("playlist-prev", "weak"))

    def stop(self) -> bool:
        return bool(self.command("stop"))

    def seek(self, seconds: float) -> bool:
        return bool(self.command("seek", f"{seconds:.2f}", "relative"))

    def set_volume(self, level: float) -> bool:
        level = max(0.0, min(100.0, float(level)))
        self.volume = level
        return bool(self.command("set_property", "volume", level))

    def adjust_volume(self, delta: float) -> float:
        current = self.status().volume or self.volume
        self.set_volume(current + delta)
        return self.volume

    def set_shuffle(self, enabled: bool) -> bool:
        self.shuffle = enabled
        return bool(self.command("set_property", "shuffle", "yes" if enabled else "no"))

    def set_repeat_file(self, enabled: bool) -> bool:
        self.repeat_file = enabled
        return bool(
            self.command("set_property", "loop-file", "inf" if enabled else "no")
        )

    def shuffle_queue(self) -> bool:
        return bool(self.command("playlist-shuffle"))

    def clear_queue(self) -> int:
        count = self.status().playlist_count
        self.command("playlist-clear")
        return count
