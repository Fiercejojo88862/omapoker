"""Canonical on-disk locations for OmaPoker.

Deliberately mirrors the layout used by omarchy-lock-studio: config and state
live under ~/.config/omarchy/<app>/, runtime state under ~/.local/state/<app>/.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "omapoker"

#: Root of the Omarchy user configuration tree.
OMARCHY_CONFIG = Path(
    os.environ.get("OMARCHY_CONFIG_DIR", Path.home() / ".config" / "omarchy")
)

#: Where profiles, bankrolls, stats and backups are kept.
CONFIG_DIR = Path(
    os.environ.get("OMAPOKER_CONFIG_DIR", OMARCHY_CONFIG / APP_NAME)
)

#: Logs and other non-precious runtime data.
STATE_DIR = Path(
    os.environ.get("OMAPOKER_STATE_DIR", Path.home() / ".local" / "state" / APP_NAME)
)

#: Application code and the managed virtualenv.
SHARE_DIR = Path(
    os.environ.get("OMAPOKER_SHARE_DIR", Path.home() / ".local" / "share" / APP_NAME)
)

# Overridable for tests via OMAPOKER_CONFIG_DIR / OMAPOKER_STATE_DIR.
DATA_DIR = CONFIG_DIR
BACKUP_DIR = CONFIG_DIR / "backups"
PROFILE_DIR = CONFIG_DIR / "profiles"
BANKROLL_DIR = CONFIG_DIR / "bankroll"
STATS_DIR = CONFIG_DIR / "stats"
LOG_DIR = STATE_DIR / "logs"

SETTINGS_FILE = CONFIG_DIR / "settings.json"
ACTIVE_PROFILE_FILE = CONFIG_DIR / "active_profile"

#: Bundled coach content. A user-editable copy in the config tree wins, then the
#: file shipped inside the package, then a source checkout.
def content_dir() -> Path:
    override = os.environ.get("OMAPOKER_CONTENT_DIR")
    if override:
        return Path(override)
    user = CONFIG_DIR / "content"
    if user.is_dir() and any(user.glob("*.json")):
        return user
    packaged = Path(__file__).resolve().parent / "content"
    if packaged.is_dir():
        return packaged
    return Path(__file__).resolve().parents[2] / "content"


def ensure_dirs() -> None:
    """Create every directory OmaPoker writes to. Safe to call repeatedly."""
    for directory in (
        CONFIG_DIR,
        BACKUP_DIR,
        PROFILE_DIR,
        BANKROLL_DIR,
        STATS_DIR,
        LOG_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)
