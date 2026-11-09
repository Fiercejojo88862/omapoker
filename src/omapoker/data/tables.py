"""A written-down note that a player is sitting at a table.

A bankroll is the one thing in OmaPoker that cannot be won back, so sitting down
is recorded *before* the cards come out. If the app is killed, the terminal is
closed, or the machine loses power, the note stays behind and the next start puts
the stack back on the bankroll instead of abandoning it on a table that no longer
exists.

The note is deleted the moment the stack is cashed in, and every recovery is
written into the ledger with the note's id, so a recovery cannot pay out twice
even if the app dies halfway through doing it.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field

from .store import IdentifiedStore, timestamp


def new_sit_id() -> str:
    """A short id for one sitting, so a recovery can be recognised later."""
    return secrets.token_hex(4)


def _now() -> str:
    return timestamp()


@dataclass
class TableSeat:
    """The stack a player has at a table, as it is written to disk."""

    profile_id: str
    level: str = ""
    buy_in: int = 0
    stack: int = 0
    sit_id: str = field(default_factory=new_sit_id)
    updated: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        return {
            "profile_id": self.profile_id,
            "level": self.level,
            "buy_in": int(self.buy_in),
            "stack": int(self.stack),
            "sit_id": self.sit_id,
            "updated": self.updated,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "TableSeat":
        return cls(
            profile_id=str(raw.get("profile_id") or ""),
            level=str(raw.get("level") or ""),
            buy_in=max(0, int(raw.get("buy_in") or 0)),
            stack=max(0, int(raw.get("stack") or 0)),
            sit_id=str(raw.get("sit_id") or new_sit_id()),
            updated=str(raw.get("updated") or _now()),
        )


class TableSeatStore(IdentifiedStore):
    """One note per profile, under ``tables/``."""

    def load_or_none(self, profile_id: str) -> TableSeat | None:
        raw = self.load(profile_id)
        if not isinstance(raw, dict):
            return None
        seat = TableSeat.from_dict(raw)
        seat.profile_id = profile_id
        return seat

    def save_seat(self, seat: TableSeat) -> None:
        self.save(seat.profile_id, seat.to_dict())

    def clear_seat(self, profile_id: str) -> bool:
        # The note is transient: keeping backups of it would only make a stale
        # sitting recoverable a second time.
        return self.delete(profile_id, keep_backup=False)
