"""The command line entry point.

Small on purpose: check the Omarchy gate, make sure the directories exist, then
hand the terminal to the interface. Everything the player can *do* lives in the
UI; everything the player can *break* lives in storage, which backs itself up.
"""

from __future__ import annotations

import argparse
import sys

from . import __version__, gate, paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="omapoker",
        description="Texas Hold'em for Omarchy systems.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"OmaPoker {__version__}",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify this system satisfies the Omarchy gate, then exit",
    )
    parser.add_argument(
        "--pretend-not-omarchy",
        action="store_true",
        help=argparse.SUPPRESS,  # debug aid: show the refusal screen on purpose
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run OmaPoker. Returns a process exit code."""
    args = build_parser().parse_args(argv)

    # Exits 3, with a branded notice and a notification, on anything but Omarchy.
    result = gate.gate_or_exit(force_refuse=args.pretend_not_omarchy)

    if args.check:
        print(f"OmaPoker {__version__} on {result.summary} - the gate is satisfied.")
        return 0

    paths.ensure_dirs()
    from .ui.app import OmaPokerApp

    OmaPokerApp().run()
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by `python -m omapoker`
    sys.exit(main())
