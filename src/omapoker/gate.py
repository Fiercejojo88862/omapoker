"""The Omarchy gate.

OmaPoker is an Omarchy application. On launch it verifies that it is running on
an Omarchy system and refuses to start otherwise, printing a branded notice and
raising a desktop notification.

Honesty note (also documented in the README): this is a *firm* check, not copy
protection. It stops a normal user on Ubuntu, Fedora, macOS or Windows/WSL dead
in their tracks. It does not and cannot stop a determined owner who is willing to
edit the script, because the script runs on their machine. There is no
obfuscation here and none is pretended.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

#: Omarchy's accent green, from /etc/os-release ANSI_COLOR (38;2;158;206;106).
ACCENT = "#9ece6a"
ERROR = "#f7768e"
DIM = "#565f89"


@dataclass
class GateResult:
    ok: bool
    os_release_id: str = ""
    pretty_name: str = ""
    build_id: str = ""
    version_id: str = ""
    omarchy_dir: str = ""
    binary: str = ""
    #: Human-readable list of failed requirements, in the order they were checked.
    failures: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if self.ok:
            return f"{self.pretty_name or 'Omarchy'} {self.build_id or self.version_id}".strip()
        return "; ".join(self.failures) if self.failures else "unknown"


def _parse_os_release(text: str) -> dict[str, str]:
    """Parse os-release into a dict, stripping quotes and handling escapes."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value.replace("\\n", "\n").replace('\\"', '"')
    return values


def check_omarchy(
    os_release: Path = Path("/etc/os-release"),
    omarchy_dir: Path = Path("/usr/share/omarchy"),
) -> GateResult:
    """Run every Omarchy requirement and report the outcome.

    Requirements, all of which must hold:
      1. /etc/os-release exists and parses.
      2. Its ID is ``omarchy`` (or it lists ``omarchy`` in ID_LIKE).
      3. The Omarchy install tree exists (default /usr/share/omarchy).
      4. The ``omarchy`` command resolves on PATH.
      5. A version string is readable from the install tree.
    """
    result = GateResult(ok=False)

    if not os_release.is_file():
        result.failures.append(f"no os-release at {os_release}")
        return result

    try:
        parsed = _parse_os_release(os_release.read_text(encoding="utf-8", errors="replace"))
    except OSError as exc:
        result.failures.append(f"cannot read {os_release}: {exc}")
        return result

    result.os_release_id = parsed.get("ID", "")
    result.pretty_name = parsed.get("PRETTY_NAME", parsed.get("NAME", ""))
    result.build_id = parsed.get("BUILD_ID", "")
    result.version_id = parsed.get("VERSION_ID", "")

    id_like = parsed.get("ID_LIKE", "").split()
    if result.os_release_id != "omarchy" and "omarchy" not in id_like:
        result.failures.append(
            f"this is not an Omarchy system (ID={result.os_release_id or 'unknown'})"
        )

    if not omarchy_dir.is_dir():
        result.failures.append(f"missing Omarchy install tree at {omarchy_dir}")
    else:
        result.omarchy_dir = str(omarchy_dir)

    binary = shutil.which("omarchy")
    if binary:
        result.binary = binary
    else:
        result.failures.append("the 'omarchy' command is not on PATH")

    if not result.build_id and not result.version_id:
        # Last resort: /usr/share/omarchy/version, present on current releases.
        version_file = omarchy_dir / "version"
        if version_file.is_file():
            try:
                result.build_id = version_file.read_text(encoding="utf-8").strip()
            except OSError:
                pass
        if not result.build_id:
            result.failures.append("no readable Omarchy version")

    result.ok = not result.failures
    return result


def _supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return sys.stdout.isatty()


def _c(text: str, color: str, bold: bool = False) -> str:
    if not _supports_color():
        return text
    weight = "\033[1m" if bold else ""
    return f"{weight}\033[38;2;{_rgb(color)}m{text}\033[0m"


def _rgb(hex_color: str) -> str:
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    return f"{r};{g};{b}"


_BANNER = r"""
   ____                 ____       _
  / __ \____ ___  ____ |  _ \ ___ | | _____ _ __
 / / / / __ `__ \/ __ \| |_) / _ \| |/ / _ \ '__|
/ /_/ / / / / / / /_/ /|  __/ (_) |   <  __/ |
\____/_/ /_/ /_/\__,_/ |_|   \___/|_|\_\___|_|
"""


def render_refusal(result: GateResult) -> str:
    """The branded screen shown when the gate fails."""
    lines = [
        _c(_BANNER, ACCENT, bold=True),
        _c("                        T E X A S   H O L D ' E M", ACCENT),
        "",
        _c("                    OmaPoker requires Omarchy.", ERROR, bold=True),
        "",
        _c("  This application refuses to start on non-Omarchy systems.", DIM),
        "",
        "  What I found:",
    ]
    if result.pretty_name:
        lines.append(f"    system        {result.pretty_name}")
    if result.os_release_id:
        lines.append(f"    os-release ID {result.os_release_id}")
    if result.build_id or result.version_id:
        lines.append(f"    version       {result.build_id or result.version_id}")

    lines.append("")
    lines.append(f"  {_c('Unmet requirements:', ERROR)}")
    for failure in result.failures:
        lines.append(f"    {_c('x', ERROR)} {failure}")

    lines += [
        "",
        _c("  Get Omarchy:  https://omarchy.org/", ACCENT),
        _c("  Manual:       https://learn.omacom.io/2/the-omarchy-manual", DIM),
        "",
    ]
    return "\n".join(lines)


def notify_refusal(result: GateResult) -> None:
    """Best-effort desktop notification. Never raises."""
    if os.environ.get("OMAPOKER_NO_NOTIFY"):
        return
    notify = shutil.which("notify-send")
    if not notify:
        return
    body = "\n".join(result.failures) or "unable to verify the system"
    try:
        subprocess.run(
            [
                notify,
                "-a",
                "OmaPoker",
                "-i",
                "dialog-error",
                "-u",
                "critical",
                "OmaPoker: Omarchy required",
                body,
            ],
            check=False,
            timeout=5,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def gate_or_exit(force_refuse: bool = False) -> GateResult:
    """Verify the system and exit(3) with a branded notice if it is not Omarchy."""
    result = check_omarchy()
    if force_refuse:
        # Debug aid: `omapoker --pretend-not-omarchy` shows the refusal screen.
        result.ok = False
        result.failures = result.failures or ["forced refusal (debug flag)"]

    if not result.ok:
        print(render_refusal(result), file=sys.stderr)
        notify_refusal(result)
        _log_refusal(result)
        raise SystemExit(3)
    return result


def _log_refusal(result: GateResult) -> None:
    try:
        from . import paths

        paths.LOG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = _now_stamp()
        with (paths.LOG_DIR / "gate-refusals.log").open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp}\t{result.summary}\t{result.failures}\n")
    except Exception:
        pass


def _now_stamp() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")
