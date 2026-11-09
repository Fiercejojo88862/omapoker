"""Chip bankroll.

Every profile starts with 100,000 chips. Chips leave the bankroll when you sit
down (the buy-in is your starting stack) and come back when you leave. Each move
is written to a ledger so a bankroll can always be explained.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .ladder import MIN_BUY_IN
from .store import IdentifiedStore

#: Chips every new profile begins with.
STARTING_CHIPS = 100_000

#: How many ledger entries to keep on disk.
LEDGER_LIMIT = 500


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class LedgerEntry:
    timestamp: str
    kind: str            # buy_in | cash_out | hand | reset | bonus
    amount: int          # signed: negative leaves the bankroll
    balance: int
    note: str = ""


@dataclass
class Bankroll:
    profile_id: str
    chips: int = STARTING_CHIPS
    created: str = field(default_factory=_now)
    ledger: list[LedgerEntry] = field(default_factory=list)

    # -- queries -------------------------------------------------------
    def can_afford(self, amount: int) -> bool:
        return amount <= self.chips

    def can_buy_in(self) -> bool:
        return self.chips >= MIN_BUY_IN

    # -- mutations -----------------------------------------------------
    def _record(self, kind: str, amount: int, note: str = "") -> LedgerEntry:
        entry = LedgerEntry(_now(), kind, amount, self.chips, note)
        self.ledger.append(entry)
        if len(self.ledger) > LEDGER_LIMIT:
            del self.ledger[: len(self.ledger) - LEDGER_LIMIT]
        return entry

    def buy_in(self, amount: int, note: str = "") -> int:
        """Take chips out to sit down with. Returns the amount removed."""
        amount = max(0, min(amount, self.chips))
        self.chips -= amount
        self._record("buy_in", -amount, note)
        return amount

    def cash_out(self, amount: int, note: str = "") -> int:
        """Return chips to the bankroll after leaving a table."""
        amount = max(0, int(amount))
        self.chips += amount
        self._record("cash_out", amount, note)
        return amount

    def settle(self, delta: int, note: str = "") -> int:
        """Apply a net win or loss."""
        self.chips = max(0, self.chips + int(delta))
        self._record("hand", int(delta), note)
        return self.chips

    def reset(self, amount: int = STARTING_CHIPS, note: str = "reset to starting chips") -> None:
        self.chips = int(amount)
        self._record("reset", self.chips, note)

    def recent(self, count: int = 20) -> list[LedgerEntry]:
        return self.ledger[-count:]

    def net_since(self, index: int = 0) -> int:
        return sum(e.amount for e in self.ledger[index:])

    # -- serialisation -------------------------------------------------
    def to_dict(self) -> dict:
        data = asdict(self)
        data["ledger"] = [asdict(e) for e in self.ledger]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Bankroll":
        bankroll = cls(
            profile_id=data.get("profile_id", ""),
            chips=int(data.get("chips", STARTING_CHIPS)),
            created=data.get("created", _now()),
        )
        bankroll.ledger = [LedgerEntry(**e) for e in data.get("ledger", [])]
        return bankroll


class BankrollStore(IdentifiedStore):
    """Loads and saves bankrolls, backing up before every write."""

    def load_or_create(self, profile_id: str) -> Bankroll:
        raw = self.load(profile_id)
        if raw is None:
            bankroll = Bankroll(profile_id=profile_id)
            self.save(profile_id, bankroll.to_dict())
            return bankroll
        bankroll = Bankroll.from_dict(raw)
        bankroll.profile_id = profile_id
        return bankroll

    def save_bankroll(self, bankroll: Bankroll) -> None:
        self.save(bankroll.profile_id, bankroll.to_dict())

    def default_root(self) -> Path:  # pragma: no cover - convenience
        return self.root
