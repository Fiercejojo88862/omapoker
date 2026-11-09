"""The little equaliser in the header, powered by ``cava``.

OmaPoker writes its own cava config rather than reusing yours, so your cava
settings are left exactly as they are. Output is cava's raw ASCII mode: each
frame is a line of ``0;0;0;`` values which map neatly onto a sparkline.

If cava is missing, or cannot attach to the audio device, the visualiser simply
reports flat bars and the rest of the app never notices.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

#: cava's ascii output is scaled to this range; we divide by it for 0..1.
ASCII_MAX = 100

DEFAULT_BARS = 12
DEFAULT_FRAMERATE = 30


def config_text(
    bars: int = DEFAULT_BARS,
    framerate: int = DEFAULT_FRAMERATE,
    input_method: str = "pulse",
    source: str = "auto",
) -> str:
    """A minimal cava config: raw output, nothing else touched."""
    return (
        "[general]\n"
        f"bars = {max(1, bars)}\n"
        f"framerate = {max(1, framerate)}\n"
        "\n"
        "[input]\n"
        f"method = {input_method}\n"
        f"source = {source}\n"
        "\n"
        "[output]\n"
        "method = raw\n"
        "raw_target = /dev/stdout\n"
        "data_format = ascii\n"
        f"ascii_max_range = {ASCII_MAX}\n"
        "\n"
        "[smoothing]\n"
        "noise_reduction = 40\n"
    )


def parse_frame(line: str, bars: int = DEFAULT_BARS) -> list[float]:
    """Turn one raw cava line into 0..1 levels."""
    values: list[float] = []
    for chunk in line.strip().split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            number = float(chunk)
        except ValueError:
            continue
        values.append(max(0.0, min(1.0, number / ASCII_MAX)))
    if not values:
        return [0.0] * bars
    if len(values) < bars:
        values.extend([0.0] * (bars - len(values)))
    return values[:bars]


@dataclass
class Visualiser:
    """A cava subprocess whose output becomes a list of levels."""

    bars: int = DEFAULT_BARS
    framerate: int = DEFAULT_FRAMERATE
    input_method: str = "pulse"
    source: str = "auto"
    binary: str = "cava"
    state_dir: Path | None = None
    levels_data: list[float] = field(default_factory=lambda: [0.0] * DEFAULT_BARS)
    started: bool = False
    error: str = ""

    def __post_init__(self) -> None:
        self.levels_data = [0.0] * self.bars
        self._process: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # -- lifecycle -----------------------------------------------------
    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def config_path(self) -> Path:
        directory = self.state_dir or Path(tempfile.gettempdir())
        return Path(directory) / "omapoker-cava.conf"

    def write_config(self) -> Path:
        path = self.config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            config_text(self.bars, self.framerate, self.input_method, self.source),
            encoding="utf-8",
        )
        return path

    def start(self) -> bool:
        if self.started:
            return True
        if not self.available():
            self.error = "cava is not installed"
            return False
        try:
            config = self.write_config()
        except OSError as exc:
            self.error = f"could not write the cava config: {exc}"
            return False
        try:
            self._process = subprocess.Popen(
                [self.binary, "-p", str(config)],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
        except OSError as exc:
            self.error = f"could not start cava: {exc}"
            self._process = None
            return False
        self._thread = threading.Thread(target=self._pump, name="omapoker-cava", daemon=True)
        self._thread.start()
        self.started = True
        return True

    def stop(self) -> None:
        process = self._process
        self._process = None
        self.started = False
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=1.5)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    process.kill()
                except OSError:
                    pass

    def alive(self) -> bool:
        return bool(self.started and self._process is not None and self._process.poll() is None)

    def ensure_running(self) -> bool:
        if self.alive():
            return True
        if self.started:
            # cava died (usually no audio device): do not thrash restarting it.
            self.error = self.error or "cava stopped"
            return False
        return self.start()

    # -- output --------------------------------------------------------
    def _pump(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            for line in process.stdout:
                frame = parse_frame(line, self.bars)
                with self._lock:
                    self.levels_data = frame
        except (OSError, ValueError):
            return
        finally:
            try:
                process.stdout.close()
            except OSError:
                pass

    def levels(self, count: int | None = None) -> list[float]:
        with self._lock:
            data = list(self.levels_data)
        if count is None or count == len(data):
            return data
        if count <= 0:
            return []
        if count < len(data):
            step = len(data) / count
            return [
                max(data[int(i * step) : max(int(i * step) + 1, int((i + 1) * step))] or [0.0])
                for i in range(count)
            ]
        # Upsampling: repeat values so the bar count stays right.
        return [data[min(len(data) - 1, int(i * len(data) / count))] for i in range(count)]

    def summary(self) -> str:
        if self.error:
            return f"visualiser off ({self.error})"
        if not self.alive():
            return "visualiser idle"
        return "visualiser running"

    def idle_pattern(self, count: int, phase: float = 0.0) -> list[float]:
        """A gentle moving pattern, for when there is no audio to show."""
        import math

        return [
            (math.sin(phase + i * 0.6) + 1) / 2 * 0.18 for i in range(max(0, count))
        ]
