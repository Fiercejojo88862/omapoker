"""Five-to-seven card hand evaluation.

The evaluator returns a plain tuple ``(category, *tiebreakers)`` whose natural
tuple ordering is the hand ordering, so ``max()`` and ``sorted()`` do the right
thing and no custom comparison operators are needed.

Categories (higher is better):

    8 straight flush      4 straight        0 high card
    7 four of a kind      3 three of a kind
    6 full house          2 two pair
    5 flush               1 one pair

Tiebreaker tuples are always padded to a fixed length per category so that
comparisons between same-category hands never raise or mis-order.
"""

from __future__ import annotations

from itertools import combinations
from typing import Iterable, Sequence

from .cards import RANK_SYMBOLS, rank_of, suit_of

STRAIGHT_FLUSH = 8
FOUR_OF_A_KIND = 7
FULL_HOUSE = 6
FLUSH = 5
STRAIGHT = 4
THREE_OF_A_KIND = 3
TWO_PAIR = 2
ONE_PAIR = 1
HIGH_CARD = 0

RANK_WORDS = (
    "Two",
    "Three",
    "Four",
    "Five",
    "Six",
    "Seven",
    "Eight",
    "Nine",
    "Ten",
    "Jack",
    "Queen",
    "King",
    "Ace",
)

RANK_PLURALS = (
    "Twos",
    "Threes",
    "Fours",
    "Fives",
    "Sixes",
    "Sevens",
    "Eights",
    "Nines",
    "Tens",
    "Jacks",
    "Queens",
    "Kings",
    "Aces",
)

CATEGORY_NAMES = {
    STRAIGHT_FLUSH: "Straight Flush",
    FOUR_OF_A_KIND: "Four of a Kind",
    FULL_HOUSE: "Full House",
    FLUSH: "Flush",
    STRAIGHT: "Straight",
    THREE_OF_A_KIND: "Three of a Kind",
    TWO_PAIR: "Two Pair",
    ONE_PAIR: "One Pair",
    HIGH_CARD: "High Card",
}

#: Number of tiebreaker slots per category, used to pad comparisons.
_TIEBREAKERS = {
    STRAIGHT_FLUSH: 1,
    FOUR_OF_A_KIND: 2,
    FULL_HOUSE: 2,
    FLUSH: 5,
    STRAIGHT: 1,
    THREE_OF_A_KIND: 3,
    TWO_PAIR: 3,
    ONE_PAIR: 4,
    HIGH_CARD: 5,
}

# Straight masks indexed by the highest rank of the straight. The wheel
# (A-2-3-4-5) is stored with its "high card" being the five, which is what
# governs its strength against other straights.
_STRAIGHT_MASKS: list[tuple[int, int]] = []
for _high in range(12, 3, -1):  # ranks 4 (six-high) .. 12 (ace-high)
    _mask = 0
    for _offset in range(5):
        _mask |= 1 << (_high - _offset)
    _STRAIGHT_MASKS.append((_mask, _high))
_WHEEL_MASK = (1 << 12) | 0b1111  # ace + 2,3,4,5
_STRAIGHT_MASKS.append((_WHEEL_MASK, 3))  # five-high straight


def _straight_high(mask: int) -> int | None:
    """Highest straight top-rank present in a rank bitmask, or None."""
    for straight_mask, high in _STRAIGHT_MASKS:
        if mask & straight_mask == straight_mask:
            return high
    return None


def evaluate(cards: Sequence[int]) -> tuple[int, ...]:
    """Evaluate 5-7 cards, returning a comparable tuple."""
    if len(cards) < 5:
        raise ValueError(f"need at least 5 cards, got {len(cards)}")
    if len(set(cards)) != len(cards):
        raise ValueError("duplicate cards in hand")

    rank_mask = 0
    suit_mask = 0
    for c in cards:
        rank_mask |= 1 << rank_of(c)
        suit_mask |= 1 << suit_of(c)

    # --- flush ---
    flush_suit = -1
    for s in range(4):
        count = 0
        for c in cards:
            if suit_of(c) == s:
                count += 1
        if count >= 5:
            flush_suit = s
            break

    if flush_suit >= 0:
        flush_mask = 0
        for c in cards:
            if suit_of(c) == flush_suit:
                flush_mask |= 1 << rank_of(c)
        sf_high = _straight_high(flush_mask)
        if sf_high is not None:
            return (STRAIGHT_FLUSH, sf_high)

    # --- rank multiplicities ---
    counts: dict[int, int] = {}
    for c in cards:
        r = rank_of(c)
        counts[r] = counts.get(r, 0) + 1

    quads = sorted((r for r, n in counts.items() if n == 4), reverse=True)
    trips = sorted((r for r, n in counts.items() if n == 3), reverse=True)
    pairs = sorted((r for r, n in counts.items() if n == 2), reverse=True)
    singles = sorted((r for r, n in counts.items() if n == 1), reverse=True)

    if quads:
        quad = quads[0]
        kicker = max((r for r in counts if r != quad), default=-1)
        return (FOUR_OF_A_KIND, quad, kicker)

    if trips:
        # A full house needs a trip plus a second group: either another trip
        # (played as the pair) or the best remaining pair.
        if len(trips) > 1:
            return (FULL_HOUSE, trips[0], trips[1])
        if pairs:
            return (FULL_HOUSE, trips[0], pairs[0])

    if flush_suit >= 0:
        flush_ranks = sorted(
            (rank_of(c) for c in cards if suit_of(c) == flush_suit), reverse=True
        )[:5]
        return (FLUSH, *flush_ranks)

    straight_high = _straight_high(rank_mask)
    if straight_high is not None:
        return (STRAIGHT, straight_high)

    if trips:
        kickers = sorted((r for r in counts if r != trips[0]), reverse=True)[:2]
        return (THREE_OF_A_KIND, trips[0], *kickers)

    if len(pairs) >= 2:
        high_pair, low_pair = pairs[0], pairs[1]
        kicker = max((r for r in counts if r not in (high_pair, low_pair)), default=-1)
        return (TWO_PAIR, high_pair, low_pair, kicker)

    if pairs:
        pair = pairs[0]
        kickers = sorted((r for r in counts if r != pair), reverse=True)[:3]
        return (ONE_PAIR, pair, *kickers)

    top_five = sorted((rank_of(c) for c in cards), reverse=True)[:5]
    return (HIGH_CARD, *top_five)


def evaluate_five(cards: Sequence[int]) -> tuple[int, ...]:
    """Evaluate exactly five cards (same representation as :func:`evaluate`)."""
    if len(cards) != 5:
        raise ValueError(f"need exactly 5 cards, got {len(cards)}")
    return evaluate(cards)


def best_of_seven(cards: Sequence[int]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Return ``(score, best_five)`` for 5-7 cards.

    The full :func:`evaluate` is already an exact best-five for seven cards, so
    the returned five-card selection is only computed on demand for display.
    """
    score = evaluate(cards)
    best: tuple[int, ...] | None = None
    if len(cards) == 5:
        best = tuple(cards)
    else:
        for combo in combinations(cards, 5):
            if evaluate(combo) == score:
                best = combo
                break
    return score, best if best is not None else tuple(cards[:5])


def hand_name(score: Sequence[int]) -> str:
    """Human name for a score tuple, e.g. ``"Two Pair, Kings and Fours"``."""
    category = score[0]

    if category == STRAIGHT_FLUSH:
        if score[1] == 3:
            return "Steel Wheel (5-high Straight Flush)"
        if score[1] == 12:
            return "Royal Flush"
        return f"{RANK_WORDS[score[1]]}-high Straight Flush"
    if category == FOUR_OF_A_KIND:
        return f"Four {RANK_PLURALS[score[1]]}"
    if category == FULL_HOUSE:
        return f"{RANK_PLURALS[score[1]]} full of {RANK_PLURALS[score[2]]}"
    if category == FLUSH:
        return f"Flush, {RANK_WORDS[score[1]]}-high"
    if category == STRAIGHT:
        if score[1] == 3:
            return "Wheel (5-high Straight)"
        return f"{RANK_WORDS[score[1]]}-high Straight"
    if category == THREE_OF_A_KIND:
        return f"Three {RANK_PLURALS[score[1]]}"
    if category == TWO_PAIR:
        return f"Two Pair, {RANK_PLURALS[score[1]]} and {RANK_PLURALS[score[2]]}"
    if category == ONE_PAIR:
        return f"Pair of {RANK_PLURALS[score[1]]}"
    return f"High Card, {RANK_WORDS[score[1]]}"


def hand_name_short(score: Sequence[int]) -> str:
    """Compact name, handy in tight UI columns."""
    category = score[0]
    if category == STRAIGHT_FLUSH and score[1] == 12:
        return "Royal Flush"
    if category == STRAIGHT_FLUSH:
        return (
            "Steel Wheel"
            if score[1] == 3
            else f"Str. Flush {RANK_SYMBOLS[score[1]]}-high"
        )
    if category == FOUR_OF_A_KIND:
        return f"Quads {RANK_SYMBOLS[score[1]]}"
    if category == FULL_HOUSE:
        return f"{RANK_SYMBOLS[score[1]]}s full of {RANK_SYMBOLS[score[2]]}s"
    if category == FLUSH:
        return f"Flush {RANK_SYMBOLS[score[1]]}-high"
    if category == STRAIGHT:
        return "Wheel" if score[1] == 3 else f"Straight {RANK_SYMBOLS[score[1]]}-high"
    if category == THREE_OF_A_KIND:
        return f"Trip {RANK_SYMBOLS[score[1]]}s"
    if category == TWO_PAIR:
        return f"{RANK_SYMBOLS[score[1]]}s up, {RANK_SYMBOLS[score[2]]}s"
    if category == ONE_PAIR:
        return f"Pair {RANK_SYMBOLS[score[1]]}s"
    return f"High card {RANK_SYMBOLS[score[1]]}"


def compare(a: Sequence[int], b: Sequence[int]) -> int:
    """-1 if ``a`` loses, 0 if tied, 1 if ``a`` wins."""
    ta, tb = tuple(a), tuple(b)
    if ta == tb:
        return 0
    return 1 if ta > tb else -1


def winners(
    hands: dict[int, Sequence[int]], boards: Sequence[int]
) -> tuple[list[int], tuple[int, ...]]:
    """Return ``(winning_ids, best_score)`` for a set of 7-card hands."""
    best_score: tuple[int, ...] | None = None
    best_ids: list[int] = []
    for player_id, hole in hands.items():
        score = evaluate(tuple(hole) + tuple(boards))
        if best_score is None or score > best_score:
            best_score = score
            best_ids = [player_id]
        elif score == best_score:
            best_ids.append(player_id)
    assert best_score is not None
    return best_ids, best_score


def _pad(score: tuple[int, ...]) -> tuple[int, ...]:
    """Pad a score to a uniform length for mixed-category storage."""
    want = 1 + _TIEBREAKERS[score[0]]
    return score + (-1,) * (want - len(score))


def score_key(score: Sequence[int]) -> tuple[int, ...]:
    """Uniform-length, sortable key for a score (used by the coach tables)."""
    return _pad(tuple(score))


def describe(cards: Iterable[int]) -> str:
    """Debug helper: ``"As Kd Qh -> A-high Straight"``."""
    from .cards import cards_str

    hand = tuple(cards)
    return f"{cards_str(hand)} -> {hand_name(evaluate(hand))}"
