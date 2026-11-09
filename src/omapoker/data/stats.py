"""Session statistics that feed the leak report.

These are the four numbers any poker coach reaches for first:

* **VPIP** - how often you voluntarily put money in preflop. Too high means you
  play too many hands.
* **PFR** - how often you raised preflop. Far below VPIP means you are calling
  too much instead of raising.
* **Aggression factor** - bets and raises divided by calls. Below 1 is passive.
* **Showdown win rate** - how often you win when cards are shown.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Sequence

from .store import IdentifiedStore

#: Actions that count as voluntary preflop money (blinds do not count).
VOLUNTARY = ("call", "bet", "raise")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class HandRecord:
    """One hand as it relates to one particular player."""

    hand_id: int = 0
    table: str = ""
    bot_level: str = ""
    played: bool = True
    won: bool = False
    showdown: bool = False
    saw_flop: bool = False
    vpip: bool = False
    pfr: bool = False
    went_all_in: bool = False
    pot_won: int = 0
    net: int = 0
    timestamp: str = field(default_factory=_now)


@dataclass
class Stats:
    profile_id: str
    hands: int = 0
    hands_won: int = 0
    showdowns: int = 0
    showdowns_won: int = 0
    flops_seen: int = 0
    vpip_hands: int = 0
    pfr_hands: int = 0
    bets: int = 0
    raises: int = 0
    calls: int = 0
    folds: int = 0
    checks: int = 0
    all_ins: int = 0
    net_chips: int = 0
    biggest_pot: int = 0
    biggest_loss: int = 0
    by_level: dict[str, int] = field(default_factory=dict)
    by_table: dict[str, int] = field(default_factory=dict)

    # -- rates ---------------------------------------------------------
    @property
    def vpip(self) -> float:
        return self.vpip_hands / self.hands if self.hands else 0.0

    @property
    def pfr(self) -> float:
        return self.pfr_hands / self.hands if self.hands else 0.0

    @property
    def win_rate(self) -> float:
        return self.hands_won / self.hands if self.hands else 0.0

    @property
    def showdown_rate(self) -> float:
        return self.showdowns / self.hands if self.hands else 0.0

    @property
    def showdown_win_rate(self) -> float:
        return self.showdowns_won / self.showdowns if self.showdowns else 0.0

    @property
    def aggression_factor(self) -> float:
        """Bets plus raises over calls. International standard, no zeros."""
        calls = self.calls or 1
        return (self.bets + self.raises) / calls

    @property
    def vpip_pfr_gap(self) -> float:
        return self.vpip - self.pfr

    def summary_lines(self) -> list[tuple[str, str]]:
        return [
            ("Hands played", f"{self.hands:,}"),
            ("Hands won", f"{self.hands_won:,} ({self.win_rate * 100:.0f}%)"),
            ("VPIP", f"{self.vpip * 100:.0f}%"),
            ("PFR", f"{self.pfr * 100:.0f}%"),
            ("Aggression factor", f"{self.aggression_factor:.2f}"),
            ("Showdowns", f"{self.showdowns:,} (won {self.showdown_win_rate * 100:.0f}%)"),
            ("Net chips", f"{self.net_chips:+,}"),
            ("Biggest pot won", f"{self.biggest_pot:,}"),
        ]

    # -- recording -----------------------------------------------------
    def record(self, record: HandRecord, actions: Sequence[str] = ()) -> None:
        """Fold one hand's outcome into the running totals."""
        self.hands += 1
        if record.won:
            self.hands_won += 1
        if record.saw_flop:
            self.flops_seen += 1
        if record.vpip:
            self.vpip_hands += 1
        if record.pfr:
            self.pfr_hands += 1
        if record.showdown:
            self.showdowns += 1
            if record.won:
                self.showdowns_won += 1
        if record.went_all_in:
            self.all_ins += 1
        self.net_chips += record.net
        self.biggest_pot = max(self.biggest_pot, record.pot_won)
        self.biggest_loss = min(self.biggest_loss, record.net)
        if record.bot_level:
            key = record.bot_level
            self.by_level[key] = self.by_level.get(key, 0) + record.net
        if record.table:
            self.by_table[record.table] = self.by_table.get(record.table, 0) + record.net

        for action in actions:
            if action == "bet":
                self.bets += 1
            elif action == "raise":
                self.raises += 1
            elif action == "call":
                self.calls += 1
            elif action == "fold":
                self.folds += 1
            elif action == "check":
                self.checks += 1

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Stats":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


def analyse_hand(hand, seat_index: int, table: str = "") -> tuple[HandRecord, list[str]]:
    """Work out what one player did in a finished hand.

    Returns the record plus the list of that player's action verbs, which the
    aggression factor is computed from.
    """
    seat = hand.seats[seat_index]
    record = HandRecord(
        hand_id=getattr(hand, "hand_id", 0),
        table=table,
        bot_level=seat.level if seat.is_bot else "",
    )

    actions: list[str] = []
    for entry in getattr(hand, "actions", []):
        if entry.seat != seat_index:
            continue
        if entry.action in ("post_sb", "post_bb"):
            continue
        actions.append(entry.action)
        if entry.street.value == "preflop" and entry.action in VOLUNTARY:
            record.vpip = True
        if entry.street.value == "preflop" and entry.action == "raise":
            record.pfr = True

    record.saw_flop = len(hand.board) >= 3 and not seat.folded
    record.went_all_in = seat.all_in

    if hand.result is not None:
        record.showdown = bool(hand.result.showdown) and not seat.folded
        gross = hand.result.totals.get(seat_index, 0)
        record.won = gross > 0
        record.pot_won = gross if record.won else 0
        record.net = gross - seat.committed_total
    return record, actions


class StatsStore(IdentifiedStore):
    def load_or_create(self, profile_id: str) -> Stats:
        raw = self.load(profile_id)
        if raw is None:
            return Stats(profile_id=profile_id)
        stats = Stats.from_dict(raw)
        stats.profile_id = profile_id
        return stats

    def save_stats(self, stats: Stats) -> None:
        self.save(stats.profile_id, stats.to_dict())
