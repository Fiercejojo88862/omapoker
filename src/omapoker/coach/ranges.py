"""Starting-hand notation and preflop ranges.

Ranges are written the way poker players write them, so the coach can show a
learner something they will see in a book or a video:

    "55+"    every pair from fives up
    "A9s+"   A9 suited, AT suited, ... up to AK suited
    "KTo+"   KT offsuit, KJ offsuit, KQ offsuit
    "T9s"    exactly ten-nine suited
    "22-66"  every pair from twos to sixes

The ranges themselves live in ``content/charts.json`` so they can be edited
without touching code, with a built-in default that matches.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from ..engine.cards import RANK_SYMBOLS, rank_of, suit_of

#: Position names, in acting order. Used to pick an opening range.
POSITIONS_EARLY = "early"
POSITIONS_MIDDLE = "middle"
POSITIONS_LATE = "late"
POSITIONS_BLINDS = "blinds"

#: A standard-ish, teachable set of opening ranges for a nine-handed table.
DEFAULT_OPEN_RANGES: dict[str, list[str]] = {
    POSITIONS_EARLY: ["77+", "ATs+", "KQs", "AJo+", "KQo"],
    POSITIONS_MIDDLE: [
        "55+",
        "A9s+",
        "KTs+",
        "QTs+",
        "JTs",
        "ATo+",
        "KJo+",
        "QJo",
    ],
    POSITIONS_LATE: [
        "22+",
        "A2s+",
        "K7s+",
        "Q8s+",
        "J8s+",
        "T8s+",
        "97s+",
        "87s+",
        "A8o+",
        "K9o+",
        "Q9o+",
        "J9o+",
        "T9o",
    ],
    POSITIONS_BLINDS: [
        "22+",
        "A2s+",
        "K5s+",
        "Q6s+",
        "J7s+",
        "T7s+",
        "96s+",
        "86s+",
        "75s+",
        "A7o+",
        "K8o+",
        "Q9o+",
        "J9o+",
        "T9o",
        "98o",
    ],
}

#: Three-betting ranges, shown when facing a raise.
DEFAULT_THREE_BET_RANGES: dict[str, list[str]] = {
    POSITIONS_EARLY: ["QQ+", "AKs", "AKo"],
    POSITIONS_MIDDLE: ["TT+", "AQs+", "AKo"],
    POSITIONS_LATE: ["99+", "AJs+", "KQs", "AJo+", "KQo"],
    POSITIONS_BLINDS: ["TT+", "AQs+", "AKo"],
}


def hand_class(hole: Sequence[int]) -> str:
    """``AKs``, ``QQ``, ``T9o`` style label for two cards."""
    if len(hole) != 2:
        raise ValueError("a hand class needs two cards")
    first, second = sorted((rank_of(c) for c in hole), reverse=True)
    if first == second:
        return f"{RANK_SYMBOLS[first]}{RANK_SYMBOLS[second]}"
    suited = suit_of(hole[0]) == suit_of(hole[1])
    return f"{RANK_SYMBOLS[first]}{RANK_SYMBOLS[second]}{'s' if suited else 'o'}"


def _split(label: str) -> tuple[int, int, str]:
    """Break ``A9s`` into (high rank, low rank, suitedness)."""
    high = RANK_SYMBOLS.index(label[0])
    low = RANK_SYMBOLS.index(label[1])
    suffix = label[2] if len(label) > 2 else ""
    return high, low, suffix


def expand(token: str) -> set[str]:
    """Expand one notation token into concrete hand classes."""
    token = token.strip()
    if not token:
        return set()

    # Ranges like "22-66".
    if "-" in token and len(token.split("-")) == 2:
        left, right = (part.strip() for part in token.split("-"))
        start = RANK_SYMBOLS.index(left[0])
        end = RANK_SYMBOLS.index(right[0])
        if start > end:
            start, end = end, start
        return {f"{RANK_SYMBOLS[r]}{RANK_SYMBOLS[r]}" for r in range(start, end + 1)}

    plus = token.endswith("+")
    body = token[:-1] if plus else token
    if len(body) < 2:
        return set()

    high, low, suffix = _split(body)

    # Pairs: "77+" means every pair from sevens up; a bare "77" is just sevens.
    if high == low:
        if plus:
            return {f"{RANK_SYMBOLS[r]}{RANK_SYMBOLS[r]}" for r in range(high, 13)}
        return {f"{RANK_SYMBOLS[high]}{RANK_SYMBOLS[low]}"}

    if not plus:
        return {f"{RANK_SYMBOLS[high]}{RANK_SYMBOLS[low]}{suffix}"}

    # Non-pairs: the low card climbs while the high card stays fixed.
    classes = set()
    for low_rank in range(low, high):
        classes.add(f"{RANK_SYMBOLS[high]}{RANK_SYMBOLS[low_rank]}{suffix}")
    return classes


def expand_all(tokens: Iterable[str]) -> set[str]:
    result: set[str] = set()
    for token in tokens:
        result |= expand(token)
    return result


def position_for(seat_index: int, button: int, num_seats: int) -> str:
    """Which range bucket a seat belongs to, from its distance from the button."""
    behind = (button - seat_index) % num_seats  # seats still to act after you
    if seat_index == button:
        return POSITIONS_LATE
    sb = (button + 1) % num_seats
    bb = (button + 2) % num_seats
    if seat_index in (sb, bb):
        return POSITIONS_BLINDS
    if behind <= 2:
        return POSITIONS_LATE
    if behind <= 4:
        return POSITIONS_MIDDLE
    return POSITIONS_EARLY


def in_range(hole: Sequence[int], tokens: Iterable[str]) -> bool:
    return hand_class(hole) in expand_all(tokens)


def describe_range(tokens: Iterable[str]) -> str:
    return ", ".join(t for t in tokens if t)


def range_percent(tokens: Iterable[str]) -> float:
    """Roughly what share of starting hands the range represents."""
    classes = expand_all(tokens)
    combos = 0
    for label in classes:
        if len(label) == 2:
            combos += 6
        elif label.endswith("s"):
            combos += 4
        else:
            combos += 12
    return combos / 1326 * 100
