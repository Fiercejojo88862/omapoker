"""Equity, outs and pot odds.

Everything here is deliberately honest about what it computes:

* :func:`equity` is a Monte-Carlo estimate against *random* hands unless the
  remaining space is small enough to enumerate exactly, in which case it is
  exact and says so via ``exact=True``. Random-hand equity overstates your
  chances when opponents have already shown strength, which the coach mentions
  rather than hides.
* :func:`count_outs` counts cards that improve you to **two pair or better**.
  That is the same definition a poker book uses when it says a flush draw has
  nine outs; it deliberately does not count "outs" that only pair your hand.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from itertools import combinations
from math import comb, factorial
from typing import Sequence

from . import cards as C
from . import evaluator as E

#: Sample budget for Monte-Carlo estimates. Exact enumeration is used when the
#: full space is no larger than this (the turn against one opponent qualifies).
EXACT_LIMIT = 5_000
DEFAULT_ITERATIONS = 4_000


@dataclass
class Equity:
    win: float
    tie: float
    lose: float
    iterations: int
    exact: bool = False

    @property
    def equity(self) -> float:
        """Win plus the share of ties you are expected to collect."""
        return self.win + self.tie / 2

    @property
    def percent(self) -> float:
        return self.equity * 100

    def describe(self) -> str:
        kind = "exact" if self.exact else "estimated"
        return (
            f"{self.percent:.1f}% equity ({kind}, {self.iterations:,} "
            f"{'deals' if self.exact else 'simulations'})"
        )


def _space_size(board_len: int, opponents: int) -> int:
    """Exact number of deals an enumeration would have to walk through.

    Counts ways to pick the opponents' hole cards (hands unordered) multiplied
    by the ways to complete the board.
    """
    remaining = 52 - 2 - board_len
    hands = (
        comb(remaining, 2 * opponents)
        * factorial(2 * opponents)
        // ((2**opponents) * factorial(opponents))
    )
    to_come = 5 - board_len
    boards = comb(remaining - 2 * opponents, to_come) if to_come else 1
    return hands * boards


def equity(
    hole: Sequence[int],
    board: Sequence[int] = (),
    opponents: int = 1,
    iterations: int = DEFAULT_ITERATIONS,
    rng: random.Random | None = None,
    force_monte_carlo: bool = False,
) -> Equity:
    """Your share of the pot against ``opponents`` random hands."""
    hole = list(hole)
    board = list(board)
    if len(hole) != 2:
        raise ValueError("a Hold'em hand is two cards")
    if opponents < 1:
        raise ValueError("need at least one opponent")

    blockers = hole + board
    if len(set(blockers)) != len(blockers):
        raise ValueError("duplicate cards between hole and board")

    deck = C.remove(C.FULL_DECK, blockers)
    to_come = 5 - len(board)

    wins = ties = trials = 0
    rng = rng or random.Random()

    for _ in range(iterations):
        pool = deck[:]
        rng.shuffle(pool)
        cursor = 0
        opponent_hands = []
        for _ in range(opponents):
            opponent_hands.append(pool[cursor : cursor + 2])
            cursor += 2
        runout = pool[cursor : cursor + to_come]
        full_board = board + runout

        hero = E.evaluate(hole + full_board)
        best_others = None
        for other in opponent_hands:
            score = E.evaluate(other + full_board)
            if best_others is None or score > best_others:
                best_others = score
        trials += 1
        if best_others is None or hero > best_others:
            wins += 1
        elif hero == best_others:
            ties += 1

    return Equity(
        win=wins / trials,
        tie=ties / trials,
        lose=(trials - wins - ties) / trials,
        iterations=trials,
        exact=False,
    )


def equity_exact(
    hole: Sequence[int], board: Sequence[int] = (), opponents: int = 1
) -> Equity:
    """Exact equity by enumerating every opponent hand and runout."""
    hole = list(hole)
    board = list(board)
    blockers = hole + board
    deck = C.remove(C.FULL_DECK, blockers)
    to_come = 5 - len(board)

    wins = ties = total = 0
    for opponent_cards in combinations(deck, opponents * 2):
        chosen = set(opponent_cards)
        rest = [c for c in deck if c not in chosen]
        opponent_hands = [
            list(opponent_cards[i : i + 2]) for i in range(0, len(opponent_cards), 2)
        ]
        if to_come == 0:
            runouts = [()]
        else:
            runouts = combinations(rest, to_come)
        for runout in runouts:
            full_board = board + list(runout)
            hero = E.evaluate(hole + full_board)
            best_others = max(E.evaluate(o + full_board) for o in opponent_hands)
            total += 1
            if hero > best_others:
                wins += 1
            elif hero == best_others:
                ties += 1

    return Equity(
        win=wins / total,
        tie=ties / total,
        lose=(total - wins - ties) / total,
        iterations=total,
        exact=True,
    )


def equity_fast(
    hole: Sequence[int],
    board: Sequence[int] = (),
    opponents: int = 1,
    iterations: int = DEFAULT_ITERATIONS,
    rng: random.Random | None = None,
) -> Equity:
    """Exact when the space is small enough, Monte Carlo otherwise."""
    if _space_size(len(board), opponents) <= EXACT_LIMIT and len(board) >= 3:
        return equity_exact(hole, board, opponents)
    return equity(hole, board, opponents, iterations, rng)


def count_outs(hole: Sequence[int], board: Sequence[int]) -> dict[int, list[int]]:
    """Cards that improve you to two pair or better, grouped by what they make.

    Returns a mapping of the resulting hand category to the list of cards.
    """
    hole = list(hole)
    board = list(board)
    current = E.evaluate(hole + board) if len(hole) + len(board) >= 5 else None
    current_category = current[0] if current else -1

    outs: dict[int, list[int]] = {}
    for card in C.remove(C.FULL_DECK, hole + board):
        score = E.evaluate(hole + board + [card])
        category = score[0]
        if category >= E.TWO_PAIR and category > current_category:
            outs.setdefault(category, []).append(card)
    return outs


def total_outs(hole: Sequence[int], board: Sequence[int]) -> int:
    """How many cards improve you to two pair or better."""
    return sum(len(cards) for cards in count_outs(hole, board).values())


def outs_summary(hole: Sequence[int], board: Sequence[int]) -> tuple[int, list[str]]:
    """Total outs plus human-readable labels of what they make."""
    grouped = count_outs(hole, board)
    total = sum(len(v) for v in grouped.values())
    labels = []
    for category in sorted(grouped, reverse=True):
        cards = grouped[category]
        labels.append(f"{len(cards)} to {E.CATEGORY_NAMES[category].lower()}")
    return total, labels


def pot_odds(call_amount: int, pot: int, extra_callers: int = 0) -> float:
    """Break-even equity needed to call, as a fraction.

    ``extra_callers`` models money still to come from players behind you, which
    lowers the price and is a genuinely important thing for a learner to see.
    """
    if call_amount <= 0:
        return 0.0
    total = pot + call_amount * (1 + extra_callers)
    return call_amount / total


def pot_odds_ratio(call_amount: int, pot: int) -> str:
    """Classic odds form, e.g. ``3.0 : 1``."""
    if call_amount <= 0:
        return "-"
    return f"{pot / call_amount:.1f} : 1"


def rule_of_thumb(outs: int, street: str) -> float:
    """The 2-and-4 rule every learner is taught, as a fraction."""
    multiplier = 4 if street == "flop" else 2
    return min(1.0, outs * multiplier / 100)


def implied_equity_needed(pot: int, call_amount: int) -> str:
    """A one-line, plain-English statement of the price being offered."""
    if call_amount <= 0:
        return "It is free to continue."
    needed = pot_odds(call_amount, pot) * 100
    return (
        f"Calling {call_amount:,} into {pot:,} needs about {needed:.0f}% equity "
        f"to break even."
    )
