"""The table ladder.

A table's buy-in **is** the stack you sit down with, and the blinds scale with
it so that every table plays at roughly 100 big blinds deep:

    big blind  = buy-in / 100
    small blind = big blind / 2

Tables run from 5,000 chips upward in 1,000-chip steps.
"""

from __future__ import annotations

from dataclasses import dataclass

MIN_BUY_IN = 5_000
MAX_BUY_IN = 100_000
BUY_IN_STEP = 1_000

#: Big blinds per buy-in. Held at 100 so stacks are deep enough to actually play
#: poker rather than shove-fests, which matters a great deal for a learning game.
BIG_BLINDS_PER_BUY_IN = 100


@dataclass(frozen=True)
class TableLevel:
    """One rung of the ladder."""

    buy_in: int
    small_blind: int
    big_blind: int
    tier: str
    name: str

    @property
    def min_buy_in(self) -> int:
        return self.buy_in

    def describe(self) -> str:
        return f"{self.buy_in:,} chips ({self.small_blind:,}/{self.big_blind:,})"


#: Display tiers, keyed by the upper bound of the buy-in.
_TIERS = (
    (10_000, "Micro"),
    (25_000, "Low"),
    (50_000, "Mid"),
    (75_000, "High"),
    (MAX_BUY_IN, "Nosebleed"),
)


def tier_for(buy_in: int) -> str:
    for ceiling, name in _TIERS:
        if buy_in <= ceiling:
            return name
    return _TIERS[-1][1]


def blinds_for(buy_in: int) -> tuple[int, int]:
    """Small and big blind for a buy-in."""
    big = max(1, buy_in // BIG_BLINDS_PER_BUY_IN)
    small = max(1, big // 2)
    return small, big


def level_for(buy_in: int) -> TableLevel:
    if buy_in < MIN_BUY_IN:
        raise ValueError(f"the minimum buy-in is {MIN_BUY_IN:,}")
    small, big = blinds_for(buy_in)
    return TableLevel(
        buy_in=buy_in,
        small_blind=small,
        big_blind=big,
        tier=tier_for(buy_in),
        name=f"{tier_for(buy_in)} {small:,}/{big:,}",
    )


def ladder(
    low: int = MIN_BUY_IN, high: int = MAX_BUY_IN, step: int = BUY_IN_STEP
) -> list[TableLevel]:
    """Every table from ``low`` to ``high`` inclusive."""
    if low % step or (high - low) % step:
        raise ValueError("buy-ins must sit on the ladder step")
    return [level_for(buy_in) for buy_in in range(low, high + 1, step)]


def ladder_by_tier() -> dict[str, list[TableLevel]]:
    grouped: dict[str, list[TableLevel]] = {}
    for level in ladder():
        grouped.setdefault(level.tier, []).append(level)
    return grouped


def affordable(bankroll: int) -> list[TableLevel]:
    """Tables a bankroll can buy into, richest first."""
    return [level for level in ladder() if level.buy_in <= bankroll]


def best_table(bankroll: int) -> TableLevel | None:
    """The richest table a bankroll can buy into."""
    options = affordable(bankroll)
    return options[-1] if options else None
