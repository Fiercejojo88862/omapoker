"""Turning the dial into actual sentences.

Every recommendation carries the arithmetic behind it, because a learner who
sees *why* a call is right stops needing to be told. Nothing here is a black
box: the equity comes from :mod:`omapoker.engine.odds`, the ranges from
:mod:`omapoker.coach.ranges`, and where a number is only an estimate the text
says so.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Sequence

from ..engine import cards as C
from ..engine import evaluator as E
from ..engine.bots import position_score, preflop_strength
from ..engine.game import Hand, LegalActions, Street
from ..engine.odds import (
    equity_fast,
    implied_equity_needed,
    outs_summary,
    pot_odds,
    pot_odds_ratio,
    rule_of_thumb,
)
from .dial import AssistDial
from .ranges import (
    DEFAULT_OPEN_RANGES,
    DEFAULT_THREE_BET_RANGES,
    describe_range,
    hand_class,
    in_range,
    position_for,
    range_percent,
)

#: Simulations used when the coach needs an equity figure.
COACH_ITERATIONS = 2_500

POSITION_NAMES = {
    "early": "early position",
    "middle": "middle position",
    "late": "late position (you act last, which is worth real money)",
    "blinds": "the blinds (you will be out of position after the flop)",
}


@dataclass
class Advice:
    """Everything the coach wants to say, sectioned by dial level."""

    hand_name: str = ""
    action: str = ""
    headline: str = ""
    reason: str = ""
    numbers: list[str] = field(default_factory=list)
    chart: list[str] = field(default_factory=list)
    walkthrough: list[str] = field(default_factory=list)
    equity: float | None = None
    exact: bool = False

    def is_empty(self) -> bool:
        return not any(
            (self.hand_name, self.action, self.numbers, self.chart, self.walkthrough)
        )


def _live_opponents(hand: Hand, seat_index: int) -> int:
    return max(1, len([s for s in hand.contenders if s.index != seat_index]))


def describe_starting_hand(hole: Sequence[int]) -> str:
    label = hand_class(hole)
    if len(label) == 2:
        return f"{label} - a pocket pair of {E.RANK_PLURALS[C.rank_of(hole[0])].lower()}"
    suited = label.endswith("s")
    return (
        f"{label} - "
        f"{E.RANK_WORDS[C.rank_of(max(hole, key=C.rank_of))]} "
        f"{E.RANK_WORDS[C.rank_of(min(hole, key=C.rank_of))]} "
        f"{'suited' if suited else 'offsuit'}"
    )


def build_advice(
    hand: Hand,
    seat_index: int,
    dial: AssistDial,
    rng: random.Random | None = None,
    open_ranges: dict[str, list[str]] | None = None,
    three_bet_ranges: dict[str, list[str]] | None = None,
) -> Advice:
    """Work out what the coach should say to a player who is to act."""
    if not dial.enabled:
        return Advice()

    rng = rng or random.Random()
    open_ranges = open_ranges or DEFAULT_OPEN_RANGES
    three_bet_ranges = three_bet_ranges or DEFAULT_THREE_BET_RANGES

    seat = hand.seats[seat_index]
    legal: LegalActions = hand.legal_actions(seat_index)
    hole = list(seat.hole)
    board = list(hand.board)
    pot = hand.total_pot
    opponents = _live_opponents(hand, seat_index)
    street = hand.street

    advice = Advice()

    # ---- level 1: what am I holding? ---------------------------------
    if street is Street.PREFLOP:
        advice.hand_name = describe_starting_hand(hole)
    elif len(board) >= 3:
        score = E.evaluate(hole + board)
        advice.hand_name = E.hand_name(score)

    # ---- level 2: the numbers ----------------------------------------
    if dial.shows_numbers:
        if street is Street.PREFLOP:
            strength = preflop_strength(hole)
            advice.numbers.append(
                f"Starting-hand strength {strength * 100:.0f}/100 "
                f"(Chen formula: high cards, pairs, suitedness, connectedness)"
            )
            estimate = equity_fast(
                hole, board, opponents=opponents, iterations=COACH_ITERATIONS, rng=rng
            )
        else:
            estimate = equity_fast(
                hole, board, opponents=opponents, iterations=COACH_ITERATIONS, rng=rng
            )
        advice.equity = estimate.equity
        advice.exact = estimate.exact
        label = "exact" if estimate.exact else "estimated"
        advice.numbers.append(
            f"Equity {estimate.percent:.0f}% against {opponents} random "
            f"{'hand' if opponents == 1 else 'hands'} ({label}; real opponents "
            f"who bet have better hands than random)"
        )
        advice.numbers.append(f"Pot {pot:,}")

        if board and len(board) >= 3:
            outs, labels = outs_summary(hole, board)
            if outs:
                multiplier = "2%" if street is Street.TURN else "4%"
                advice.numbers.append(
                    f"Outs: {outs} ({'; '.join(labels)}) - the "
                    f"rule of thumb says about {outs} x {multiplier} = "
                    f"{rule_of_thumb(outs, street.value) * 100:.0f}% to improve"
                )
            else:
                advice.numbers.append("Outs: none that make two pair or better")

        if legal.can_call:
            needed = pot_odds(legal.call_amount, pot)
            advice.numbers.append(
                f"Pot odds: calling {legal.call_amount:,} into {pot:,} pays "
                f"{pot_odds_ratio(legal.call_amount, pot)} - you need "
                f"{needed * 100:.0f}% to break even"
            )
        else:
            advice.numbers.append("Nothing to call: you can check or bet")

    # ---- level 3: what should I do? ----------------------------------
    if dial.shows_advice:
        equity = advice.equity
        if equity is None:
            equity = equity_fast(
                hole, board, opponents=opponents, iterations=COACH_ITERATIONS, rng=rng
            ).equity
            advice.equity = equity

        action, headline, reason = _recommend(hand, seat_index, legal, equity, street, hole)
        advice.action = action
        advice.headline = headline
        advice.reason = reason

    # ---- level 4: ranges and position --------------------------------
    if dial.shows_charts:
        position = position_for(seat_index, hand.button, hand.n)
        facing_raise = hand.current_bet > hand.big_blind and street is Street.PREFLOP
        table = three_bet_ranges if facing_raise else open_ranges
        tokens = table.get(position, [])
        advice.chart.append(
            f"You are in {POSITION_NAMES.get(position, position)}"
        )
        if street is Street.PREFLOP:
            verb = "re-raising" if facing_raise else "opening"
            inside = in_range(hole, tokens)
            advice.chart.append(
                f"Your {verb} range here is about {range_percent(tokens):.0f}% of "
                f"hands: {describe_range(tokens)}"
            )
            advice.chart.append(
                f"{hand_class(hole)} is {'IN' if inside else 'NOT in'} that range"
            )
        else:
            advice.chart.append(
                "Post-flop position matters more than any chart: acting last "
                "means you see what they do before you decide."
            )
        advice.chart.append(
            f"You act at position {position_score(hand, seat_index) * 100:.0f}/100 "
            f"of the table (100 is last to act)"
        )

    # ---- level 5: walk me through it ---------------------------------
    if dial.shows_walkthrough:
        advice.walkthrough = _walkthrough(hand, seat_index, legal, advice, street, board)

    return advice


def _recommend(
    hand: Hand,
    seat_index: int,
    legal: LegalActions,
    equity: float,
    street: Street,
    hole: Sequence[int],
) -> tuple[str, str, str]:
    """The headline recommendation, with its reason."""
    pot = hand.total_pot

    if legal.can_check:
        if equity >= 0.62:
            return (
                "Bet",
                f"Bet for value - you are a strong favourite at {equity * 100:.0f}%",
                "When you are ahead, betting is how you get paid. Check-calling a "
                "big hand wins smaller pots than betting it does.",
            )
        if equity <= 0.34:
            return (
                "Check",
                "Check - you are not strong enough to bet for value",
                "Betting a weak hand here mostly gets called by better hands. Take "
                "the free card instead.",
            )
        return (
            "Check",
            "Check - you are in between, so control the pot",
            "With a medium hand, keeping the pot small is usually better than "
            "building it and then having to fold.",
        )

    if not legal.can_call:
        return ("Check", "Check", "There is nothing to call.")

    needed = pot_odds(legal.call_amount, pot)
    margin = equity - needed

    if margin >= 0.22 and legal.can_raise:
        return (
            "Raise",
            f"Raise - you are well ahead of the price at {equity * 100:.0f}% "
            f"versus {needed * 100:.0f}% needed",
            "Raising both charges weaker hands to continue and builds the pot "
            "while you hold the best of it.",
        )
    if margin >= 0.0:
        return (
            "Call",
            f"Call - you have {equity * 100:.0f}% equity and only need "
            f"{needed * 100:.0f}% to break even",
            "You are getting the right price. Not by much, so calling rather than "
            "raising keeps the risk down.",
        )
    return (
        "Fold",
        f"Fold - {equity * 100:.0f}% equity does not cover the {needed * 100:.0f}% "
        f"you need",
        "Calling here loses money in the long run, even when you sometimes win the "
        "hand. The price is simply wrong.",
    )


def _walkthrough(
    hand: Hand,
    seat_index: int,
    legal: LegalActions,
    advice: Advice,
    street: Street,
    board: Sequence[int],
) -> list[str]:
    """A numbered, step-by-step version of the decision."""
    steps: list[str] = []
    seat = hand.seats[seat_index]

    steps.append(
        f"1. Cards: you hold {C.cards_glyphs(seat.hole)}"
        + (f" on a {C.cards_glyphs(board)} board" if board else " preflop")
        + f". Best five cards: {advice.hand_name or 'unknown'}."
    )
    steps.append(
        f"2. Situation: {len(hand.contenders) - 1} opponent(s) still in, "
        f"pot {hand.total_pot:,}, it costs "
        f"{legal.call_amount:,} to continue."
        if legal.can_call
        else f"2. Situation: {len(hand.contenders) - 1} opponent(s) still in and "
        f"pot {hand.total_pot:,}, with nothing to call."
    )
    if advice.equity is not None:
        steps.append(
            f"3. Equity: about {advice.equity * 100:.0f}% to win against random "
            f"hands. Treat that as an optimistic ceiling, because opponents who "
            f"have put money in hold better than random cards."
        )
    if legal.can_call:
        needed = pot_odds(legal.call_amount, hand.total_pot) * 100
        steps.append(
            f"4. Price: you need {needed:.0f}% to justify the call. "
            f"{implied_equity_needed(hand.total_pot, legal.call_amount)}"
        )
    steps.append(f"5. Decision: {advice.headline}")
    steps.append(f"6. Why: {advice.reason}")
    steps.append(
        "7. Remember: the point is not this hand, it is repeating decisions like "
        "this one thousands of times."
    )
    return steps


def review_hand(outcome, seats, seat_index: int, dial: AssistDial) -> list[str]:
    """A post-hand review, shown only at the top dial level."""
    if not dial.shows_review or outcome is None:
        return []

    lines: list[str] = []
    board = outcome.board
    if board:
        lines.append(f"Board: {C.cards_glyphs(board)}")

    mine = outcome.reveal.get(seat_index)
    if mine:
        score = E.evaluate(list(mine) + list(board)) if len(board) == 5 else None
        lines.append(f"Your hand: {C.cards_glyphs(mine)}")
        if score:
            lines.append(f"Best five: {E.hand_name(score)}")

    net = outcome.net.get(seat_index, 0)
    lines.append(
        f"You {'won' if net > 0 else 'lost' if net < 0 else 'broke even on'} "
        f"{abs(net):,} chips this hand."
    )

    if outcome.showdown and outcome.winning_score is not None:
        lines.append(f"Winning hand: {E.hand_name(outcome.winning_score)}")
        for index, hole in sorted(outcome.reveal.items()):
            if index == seat_index:
                continue
            name = seats[index].name if index < len(seats) else f"seat {index}"
            score = E.evaluate(list(hole) + list(board)) if len(board) == 5 else None
            shown = E.hand_name(score) if score else "unknown"
            lines.append(f"{name} showed {C.cards_glyphs(hole)} ({shown})")
    elif not outcome.showdown:
        lines.append("Everyone folded, so no cards were shown.")

    lines.append(
        "Ask yourself: would you make the same decision again, knowing only what "
        "you knew at the time? That is the only question that matters."
    )
    return lines
