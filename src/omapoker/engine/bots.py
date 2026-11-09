"""Bot opponents.

Bots exist so a learner always has a game. They are rule-based rather than
sentient, and each difficulty is a genuinely different kind of opponent rather
than the same brain with the numbers nudged:

    Easy    a loose calling station. Rarely folds, rarely raises, sizes badly.
    Medium  plays its cards. Knows pot odds, bets for value, bluffs a little.
    Hard    plays position, semi-bluffs draws, sizes by pot fraction.
    Pro     balanced: value bets, bluffs, and adjusts bet size to the situation.

Crucially, **bot behaviour never depends on the assist dial**. Your coach level
changes what you are told, not how well the table plays, so results stay honest.

Preflop uses a fast explainable hand-strength score (the Chen formula) rather
than Monte-Carlo, because simulating preflop equity for every decision would
make the table crawl. Postflop uses real equity from :mod:`omapoker.engine.odds`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Sequence

from . import cards as C
from .game import Action, Hand, Seat, Street
from .odds import equity_fast, pot_odds, total_outs

#: Bot names, chosen so a table reads clearly.
BOT_NAMES = (
    "Ada",
    "Boris",
    "Cleo",
    "Dmitri",
    "Esme",
    "Figaro",
    "Greta",
    "Hugo",
)


@dataclass(frozen=True)
class BotProfile:
    """Everything that makes one difficulty feel different from another."""

    level: str
    label: str
    iterations: int
    mistake_rate: float
    aggression: float
    bluff_frequency: float
    call_margin: float
    value_threshold: float
    raise_threshold: float
    position_weight: float
    sizing: float
    can_semibluff: bool

    def describe(self) -> str:
        return f"{self.label} ({self.level})"


PROFILES: dict[str, BotProfile] = {
    "easy": BotProfile(
        level="easy",
        label="Easy",
        iterations=120,
        mistake_rate=0.35,
        aggression=0.5,
        bluff_frequency=0.02,
        call_margin=-0.04,      # calls too much: the classic learner mistake
        value_threshold=0.80,
        raise_threshold=0.88,
        position_weight=0.0,
        sizing=0.5,
        can_semibluff=False,
    ),
    "medium": BotProfile(
        level="medium",
        label="Medium",
        iterations=350,
        mistake_rate=0.15,
        aggression=1.0,
        bluff_frequency=0.06,
        call_margin=0.03,
        value_threshold=0.62,
        raise_threshold=0.72,
        position_weight=0.04,
        sizing=0.6,
        can_semibluff=False,
    ),
    "hard": BotProfile(
        level="hard",
        label="Hard",
        iterations=650,
        mistake_rate=0.06,
        aggression=1.2,
        bluff_frequency=0.10,
        call_margin=0.05,
        value_threshold=0.56,
        raise_threshold=0.66,
        position_weight=0.08,
        sizing=0.7,
        can_semibluff=True,
    ),
    "pro": BotProfile(
        level="pro",
        label="Pro",
        iterations=1000,
        mistake_rate=0.02,
        aggression=1.3,
        bluff_frequency=0.14,
        call_margin=0.06,
        value_threshold=0.52,
        raise_threshold=0.60,
        position_weight=0.10,
        sizing=0.75,
        can_semibluff=True,
    ),
}


def profile_for(level: str) -> BotProfile:
    return PROFILES.get(level, PROFILES["medium"])


# --------------------------------------------------------------------------
# hand strength
# --------------------------------------------------------------------------

def preflop_strength(hole: Sequence[int]) -> float:
    """The Chen formula, normalised to roughly 0..1.

    Chen scores a starting hand out of about 20 by weighting high cards, pairs,
    suitedness, connectedness and straight gaps. It is a real, teachable rule of
    thumb rather than a magic number, which is exactly what a beginner coach
    wants to be able to explain.
    """
    if len(hole) != 2:
        raise ValueError("preflop strength needs two cards")
    ranks = sorted((C.rank_of(c) for c in hole), reverse=True)
    high, low = ranks
    suited = C.suit_of(hole[0]) == C.suit_of(hole[1])

    # High card value: aces 10, kings 8, queens 7, jacks 6, else half the rank.
    if high == 12:
        score = 10.0
    elif high == 11:
        score = 8.0
    elif high == 10:
        score = 7.0
    elif high == 9:
        score = 6.0
    else:
        score = (high + 2) / 2.0

    if high == low:
        # Pairs: minimum 5, and the low pairs get a bump.
        score = max(score * 2, 5.0)

    if suited:
        score += 2.0

    gap = high - low - 1
    if high != low:
        if gap == 1:
            score -= 1
        elif gap == 2:
            score -= 2
        elif gap == 3:
            score -= 4
        elif gap >= 4:
            score -= 5

    if gap <= 1 and high < 10 and high != low:
        score += 1  # connectedness bonus for small connectors

    return max(0.0, min(1.0, score / 20.0))


def made_hand_strength(hand: Hand, seat_index: int, profile: BotProfile, rng: random.Random) -> float:
    """Equity-based strength for flop, turn and river."""
    seat = hand.seats[seat_index]
    opponents = max(
        1, len([s for s in hand.contenders if s.index != seat_index])
    )
    estimate = equity_fast(
        seat.hole,
        hand.board,
        opponents=opponents,
        iterations=profile.iterations,
        rng=rng,
    )
    return estimate.equity


def position_score(hand: Hand, seat_index: int) -> float:
    """0 for the worst seat, 1 for the last to act postflop."""
    order = [(hand.button + step) % hand.n for step in range(1, hand.n + 1)]
    live = [i for i in order if not hand.seats[i].folded and not hand.seats[i].sitting_out]
    if len(live) <= 1 or seat_index not in live:
        return 0.0
    return live.index(seat_index) / (len(live) - 1)


# --------------------------------------------------------------------------
# decisions
# --------------------------------------------------------------------------

def _round_to_chips(amount: int, big_blind: int) -> int:
    """Round a bet to something that reads like a real bet."""
    unit = max(1, big_blind // 2)
    return max(unit, int(round(amount / unit)) * unit)


def _clamp_amount(legal, amount: int, is_bet: bool) -> int:
    """Force an amount into the engine's legal window.

    Rounding a bet to a tidy number can push it below the minimum, so the final
    value is always clamped. Landing exactly on the maximum is fine: the engine
    treats that as an all-in, which may legally be short of a full raise.
    """
    if is_bet:
        low = legal.min_bet
    else:
        low = legal.min_raise_to
    high = legal.max_raise_to
    if high <= low:
        return high
    return max(low, min(amount, high))


def _size_bet(hand: Hand, legal, profile: BotProfile, strength: float, rng: random.Random) -> int:
    """Pick a bet or raise target: a pot fraction, tinted by hand strength."""
    pot = hand.total_pot
    current = hand.current_bet
    fraction = profile.sizing * (0.6 + 0.8 * strength)
    fraction = max(0.35, min(1.5, fraction))
    if legal.can_bet:
        target = int(pot * fraction)
        target = max(target, legal.min_bet)
    else:
        target = current + int((pot + current) * fraction)
        target = max(target, legal.min_raise_to)
    target = min(target, legal.max_raise_to)
    return max(0, min(target, legal.max_raise_to))


def decide(
    hand: Hand,
    seat_index: int,
    profile: BotProfile,
    rng: random.Random | None = None,
) -> tuple[Action, int]:
    """Choose a legal action for a bot.

    Guaranteed to return something the engine will accept, so a bot can never
    stall a hand.
    """
    rng = rng or random.Random()
    legal = hand.legal_actions(seat_index)
    seat = hand.seats[seat_index]

    # Safety net: never return an illegal action.
    def fallback() -> tuple[Action, int]:
        if legal.can_check:
            return Action.CHECK, 0
        if legal.can_call:
            return Action.CALL, legal.call_amount
        return Action.FOLD, 0

    if seat.folded or seat.stack == 0:
        return fallback()

    # ---- strength -----------------------------------------------------
    if hand.street is Street.PREFLOP:
        strength = preflop_strength(seat.hole)
    else:
        strength = made_hand_strength(hand, seat_index, profile, rng)

    if profile.position_weight:
        strength += profile.position_weight * (position_score(hand, seat_index) - 0.5)

    if profile.mistake_rate and rng.random() < profile.mistake_rate:
        strength += rng.uniform(-0.18, 0.18)
    strength = max(0.0, min(1.0, strength))

    pot = hand.total_pot
    contenders = len(hand.contenders)

    # ---- nobody has bet: check or bet ---------------------------------
    if legal.can_check or legal.can_bet:
        wants_value = strength >= profile.value_threshold
        wants_bluff = (
            rng.random() < profile.bluff_frequency
            and strength < profile.value_threshold
            and legal.can_bet
        )
        semi_bluff = False
        if profile.can_semibluff and legal.can_bet and hand.board:
            semi_bluff = total_outs(seat.hole, hand.board) >= 8 and rng.random() < 0.45

        if legal.can_bet and (wants_value or wants_bluff or semi_bluff):
            target = _size_bet(hand, legal, profile, strength, rng)
            return Action.BET, _clamp_amount(
                legal, _round_to_chips(target, hand.big_blind), is_bet=True
            )
        return Action.CHECK, 0

    # ---- facing a bet --------------------------------------------------
    needed = pot_odds(legal.call_amount, pot)
    margin = profile.call_margin
    # Multiway pots need more than the raw price: more hands can beat you.
    if contenders > 2:
        margin += 0.03 * (contenders - 2)
    # Short stacks that are pot-committed should not be folding for a pittance.
    if legal.call_amount >= seat.stack * 0.9 and strength > needed:
        return Action.CALL, legal.call_amount

    good = strength >= needed + margin

    if not good:
        # Bluff-raise occasionally, more often with a real draw.
        outs = total_outs(seat.hole, hand.board) if hand.board else 0
        bluff_raise = (
            legal.can_raise
            and strength > 0.22
            and rng.random() < profile.bluff_frequency * (1.4 if outs >= 8 else 0.6)
        )
        if bluff_raise:
            target = _size_bet(hand, legal, profile, max(strength, 0.5), rng)
            return Action.RAISE, _clamp_amount(
                legal, _round_to_chips(target, hand.big_blind), is_bet=False
            )
        if legal.can_fold:
            return Action.FOLD, 0
        return fallback()

    # Raising for value.
    if (
        legal.can_raise
        and strength >= profile.raise_threshold
        and rng.random() < min(0.85, 0.35 + 0.4 * profile.aggression)
    ):
        target = _size_bet(hand, legal, profile, strength, rng)
        return Action.RAISE, _clamp_amount(
            legal, _round_to_chips(target, hand.big_blind), is_bet=False
        )

    return Action.CALL, legal.call_amount


def act_for(
    hand: Hand, seat_index: int, profile: BotProfile, rng: random.Random | None = None
) -> None:
    """Decide and immediately apply a bot's action."""
    action, amount = decide(hand, seat_index, profile, rng)
    legal = hand.legal_actions(seat_index)
    # Guard rails: clamp to legal bounds so an odd rounding can never throw.
    if action is Action.BET and legal.can_bet:
        amount = max(legal.min_bet, min(amount, legal.max_raise_to))
    elif action is Action.RAISE and legal.can_raise:
        if amount != legal.max_raise_to:
            amount = max(legal.min_raise_to, min(amount, legal.max_raise_to))
    elif action is Action.CALL:
        amount = legal.call_amount
    hand.act(action, amount)


def make_bot(seat_index: int, name: str, stack: int, level: str) -> Seat:
    profile = profile_for(level)
    return Seat(
        index=seat_index,
        name=name,
        stack=stack,
        is_human=False,
        is_bot=True,
        level=profile.level,
        bot_name=name,
    )


def suggest_bot_names(count: int, taken: Sequence[str] = ()) -> list[str]:
    available = [n for n in BOT_NAMES if n not in set(taken)]
    return (available * 2)[:count]
