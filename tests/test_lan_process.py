"""Keep the host and guest honest across separate Python/Textual processes."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time


def test_host_and_guest_play_multiple_hands_in_separate_processes(tmp_path):
    script = Path(__file__).with_name("lan_peer.py")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(script.parents[1] / "src")
    peers = []
    try:
        for role in ("host", "guest"):
            with (tmp_path / f"{role}.log").open("w") as log:
                process = subprocess.Popen(
                    [sys.executable, str(script), role, str(tmp_path)],
                    env=env, stdout=log, stderr=subprocess.STDOUT,
                )
            peers.append(process)
        deadline = time.monotonic() + 45.0
        for process in peers:
            try:
                process.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                break
    finally:
        for process in peers:
            if process.poll() is None:
                process.kill()
            process.wait()

    diagnostics = "\n".join(
        f"{path.name}:\n{path.read_text()[-10000:]}"
        for path in sorted(tmp_path.iterdir())
        if path.suffix in (".log", ".progress")
    )
    assert len(peers) == 2 and all(process.returncode == 0 for process in peers), diagnostics
