"""Persistence: profiles, identities, bankrolls, stats and settings."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .. import paths
from .bankroll import STARTING_CHIPS, Bankroll, BankrollStore, LedgerEntry
from .ladder import (
    BIG_BLINDS_PER_BUY_IN,
    BUY_IN_STEP,
    MAX_BUY_IN,
    MIN_BUY_IN,
    TableLevel,
    affordable,
    best_table,
    blinds_for,
    ladder,
    ladder_by_tier,
    level_for,
    tier_for,
)
from .profiles import (
    EXPERIENCE_ASSIST,
    EXPERIENCE_LABELS,
    EXPERIENCE_LEVELS,
    Identity,
    Profile,
    code_for_public_key,
    code_matches_key,
    crypto_available,
    normalise_code,
    verify_signature,
)
from .stats import HandRecord, Stats, StatsStore, analyse_hand
from .store import IdentifiedStore, JsonStore, atomic_write_json, read_json
from .tables import TableSeat, TableSeatStore, new_sit_id

DEFAULT_SETTINGS = {
    "cards_style": "glyphs",
    "action_clock": 30,
    "lan_port": 8760,
    "host_name": "OmaPoker table",
    "show_hints_by_default": True,
    "visualiser": True,
    "last_seen_version": "",
}


@dataclass
class PlayerData:
    """Everything bound to one profile, loaded together."""

    profile: Profile
    bankroll: Bankroll
    stats: Stats
    #: A guest (a hot-seat friend) plays for the evening and is never written to
    #: disk. Their chips are throwaway, so they cannot pollute the bankroll tree.
    guest: bool = False

    @property
    def name(self) -> str:
        return self.profile.name

    @property
    def chips(self) -> int:
        return self.bankroll.chips


class DataStore:
    """The single entry point for everything OmaPoker persists."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root else paths.CONFIG_DIR
        self.profile_store = IdentifiedStore(self.root / "profiles")
        self.bankroll_store = BankrollStore(self.root / "bankroll")
        self.stats_store = StatsStore(self.root / "stats")
        self.settings_store = JsonStore(self.root)
        self.table_store = TableSeatStore(self.root / "tables")
        self.backup_root = self.root / "backups"
        self.root.mkdir(parents=True, exist_ok=True)

    # -- settings ------------------------------------------------------
    def settings(self) -> dict:
        stored = self.settings_store.load("settings", {}) or {}
        merged = dict(DEFAULT_SETTINGS)
        merged.update(stored)
        return merged

    def save_settings(self, settings: dict) -> None:
        merged = dict(DEFAULT_SETTINGS)
        merged.update(settings)
        self.settings_store.save("settings", merged)

    def update_setting(self, key: str, value) -> dict:
        settings = self.settings()
        settings[key] = value
        self.save_settings(settings)
        return settings

    # -- profiles ------------------------------------------------------
    def list_profiles(self) -> list[Profile]:
        profiles = []
        for identifier in self.profile_store.identifiers():
            raw = self.profile_store.load(identifier)
            if raw:
                profile = Profile.from_dict(raw)
                profile.id = identifier
                profiles.append(profile)
        profiles.sort(key=lambda p: (p.last_seen or "", p.name), reverse=True)
        return profiles

    def create_profile(self, name: str, experience: str = "casual") -> Profile:
        identifier = self.profile_store.unique_slug(name)
        profile = Profile.new(identifier, name.strip() or identifier, experience)
        self.save_profile(profile)
        # A new profile gets its starting bankroll straight away.
        bankroll = Bankroll(profile_id=identifier, chips=STARTING_CHIPS)
        bankroll._record("start", STARTING_CHIPS, "starting bankroll")
        self.bankroll_store.save_bankroll(bankroll)
        self.stats_store.save_stats(Stats(profile_id=identifier))
        self.set_active_profile(identifier)
        return profile

    def load_profile(self, profile_id: str) -> Profile | None:
        raw = self.profile_store.load(profile_id)
        if raw is None:
            return None
        profile = Profile.from_dict(raw)
        profile.id = profile_id
        return profile

    def save_profile(self, profile: Profile) -> None:
        from datetime import datetime

        profile.last_seen = datetime.now().isoformat(timespec="seconds")
        self.profile_store.save(profile.id, profile.to_dict())

    def delete_profile(self, profile_id: str, purge_chips: bool = True) -> bool:
        removed = self.profile_store.delete(profile_id)
        if purge_chips:
            self.bankroll_store.delete(profile_id)
            self.stats_store.delete(profile_id)
            self.table_store.clear_seat(profile_id)
        if self.active_profile_id() == profile_id:
            self.active_profile_file().unlink(missing_ok=True)
        return removed

    # -- active profile ------------------------------------------------
    def active_profile_file(self) -> Path:
        return self.root / "active_profile"

    def active_profile_id(self) -> str | None:
        try:
            value = self.active_profile_file().read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return value or None

    def set_active_profile(self, profile_id: str) -> None:
        self.active_profile_file().write_text(profile_id + "\n", encoding="utf-8")

    def active_profile(self) -> Profile | None:
        identifier = self.active_profile_id()
        if identifier:
            profile = self.load_profile(identifier)
            if profile:
                return profile
        profiles = self.list_profiles()
        return profiles[0] if profiles else None

    # -- bundles -------------------------------------------------------
    def bankroll_for(self, profile_id: str) -> Bankroll:
        return self.bankroll_store.load_or_create(profile_id)

    def stats_for(self, profile_id: str) -> Stats:
        return self.stats_store.load_or_create(profile_id)

    def player(self, profile_id: str) -> PlayerData | None:
        profile = self.load_profile(profile_id)
        if profile is None:
            return None
        return PlayerData(
            profile=profile,
            bankroll=self.bankroll_for(profile_id),
            stats=self.stats_for(profile_id),
        )

    def save_player(self, player: PlayerData) -> None:
        self.save_profile(player.profile)
        self.bankroll_store.save_bankroll(player.bankroll)
        self.stats_store.save_stats(player.stats)

    # -- a sitting, on disk --------------------------------------------
    def save_table_seat(self, seat: TableSeat) -> None:
        """Note that this profile is at a table, and with how many chips."""
        self.table_store.save_seat(seat)

    def table_seat(self, profile_id: str) -> TableSeat | None:
        return self.table_store.load_or_none(profile_id)

    def clear_table_seat(self, profile_id: str) -> None:
        self.table_store.clear_seat(profile_id)

    def recover_table_seat(self, player: PlayerData) -> tuple[int, str] | None:
        """Put a stack back on the bankroll after an unclean exit.

        Only the app being killed or the terminal being closed gets here: a
        normal exit cashes in and deletes the note first. The ledger records the
        sitting's id, so a crash halfway through a recovery is not paid twice.
        """
        identifier = player.profile.id
        seat = self.table_seat(identifier)
        if seat is None:
            return None
        note = f"recovered {seat.level} (sitting {seat.sit_id})"
        if any(entry.note == note for entry in player.bankroll.ledger):
            self.clear_table_seat(identifier)
            return None
        if seat.stack <= 0:
            self.clear_table_seat(identifier)
            return None
        player.bankroll.cash_out(seat.stack, note)
        self.bankroll_store.save_bankroll(player.bankroll)
        self.clear_table_seat(identifier)
        return seat.stack, seat.level or "a table"


__all__ = [
    "BIG_BLINDS_PER_BUY_IN",
    "BUY_IN_STEP",
    "DEFAULT_SETTINGS",
    "EXPERIENCE_ASSIST",
    "EXPERIENCE_LABELS",
    "EXPERIENCE_LEVELS",
    "MAX_BUY_IN",
    "MIN_BUY_IN",
    "PlayerData",
    "STARTING_CHIPS",
    "DataStore",
    "Bankroll",
    "BankrollStore",
    "HandRecord",
    "IdentifiedStore",
    "Identity",
    "JsonStore",
    "LedgerEntry",
    "Profile",
    "Stats",
    "StatsStore",
    "TableLevel",
    "TableSeat",
    "TableSeatStore",
    "affordable",
    "analyse_hand",
    "atomic_write_json",
    "best_table",
    "blinds_for",
    "code_for_public_key",
    "code_matches_key",
    "crypto_available",
    "ladder",
    "ladder_by_tier",
    "level_for",
    "normalise_code",
    "read_json",
    "tier_for",
    "verify_signature",
]
