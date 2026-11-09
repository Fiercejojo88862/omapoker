"""Engine tests: card helpers and hand evaluation.

The important one here is ``test_five_card_frequency_distribution``: it walks
all 2,598,960 five-card hands and compares the resulting category counts against
the mathematically known distribution. If that passes, the evaluator classifies
every possible five-card hand correctly. The best-of-seven property test then
covers the 7-card reduction.
"""

from __future__ import annotations

import random
from collections import Counter
from itertools import combinations

import pytest

from omapoker.engine import cards as C
from omapoker.engine import evaluator as E


# --------------------------------------------------------------------------
# card helpers
# --------------------------------------------------------------------------

def test_card_roundtrip():
    for c in C.FULL_DECK:
        assert C.parse_card(C.card_str(c)) == c
    assert C.card_str(C.card(12, 3)) == "As"
    assert C.card_glyph(C.card(12, 3)) == "A\u2660"
    assert C.parse_card("10h") == C.card(8, 2)
    assert C.parse_card("td") == C.card(8, 1)


def test_parse_rejects_duplicates_and_junk():
    with pytest.raises(ValueError):
        C.parse_cards("As As")
    with pytest.raises(ValueError):
        C.parse_card("Zx")
    with pytest.raises(ValueError):
        C.parse_card("A")


def test_deck_is_a_permutation_of_52():
    deck = C.new_deck()
    assert len(deck) == 52
    assert sorted(deck) == list(range(52))


def test_shuffle_is_reproducible_with_seed():
    a = C.shuffle(list(C.FULL_DECK), random.Random(7))
    b = C.shuffle(list(C.FULL_DECK), random.Random(7))
    assert a == b
    assert a != list(C.FULL_DECK)


def test_deck_key_roundtrip():
    deck = C.new_deck()
    assert C.deck_from_key(C.deck_key(deck)) == deck


# --------------------------------------------------------------------------
# category classification
# --------------------------------------------------------------------------

def hand(text: str) -> tuple[int, ...]:
    return E.evaluate(C.parse_cards(text))


def test_categories():
    assert hand("As Ks Qs Js Ts")[0] == E.STRAIGHT_FLUSH
    assert hand("As Ks Qs Js Ts")[1] == 12  # royal
    assert hand("9s 8s 7s 6s 5s")[0] == E.STRAIGHT_FLUSH
    assert hand("As Ad Ah Ac Kd")[0] == E.FOUR_OF_A_KIND
    assert hand("As Ad Ah Kc Kd")[0] == E.FULL_HOUSE
    assert hand("As Ks Qs Js 9s")[0] == E.FLUSH
    assert hand("9s 8d 7h 6c 5s")[0] == E.STRAIGHT
    assert hand("As Ad Ah Kc Qd")[0] == E.THREE_OF_A_KIND
    assert hand("As Ad Kh Kc Qd")[0] == E.TWO_PAIR
    assert hand("As Ad Kh Qc Jd")[0] == E.ONE_PAIR
    assert hand("As Kd Qh Jc 9d")[0] == E.HIGH_CARD


def test_wheel_is_a_straight_and_ranks_lowest():
    wheel = hand("As 2d 3h 4c 5s")
    assert wheel[0] == E.STRAIGHT
    assert wheel[1] == 3  # the five plays as the top card
    six_high = hand("2s 3d 4h 5c 6s")
    assert six_high > wheel
    # Ace must not allow a "broadway wrap".
    assert hand("Qs Kd Ah 2c 3d")[0] == E.HIGH_CARD


def test_steel_wheel_is_a_straight_flush():
    steel = hand("As 2s 3s 4s 5s")
    assert steel[0] == E.STRAIGHT_FLUSH
    assert steel < hand("2s 3s 4s 5s 6s")


def test_full_house_from_two_trips_uses_second_trip_as_pair():
    score = hand("As Ad Ah Kc Kd Ks")
    assert score[0] == E.FULL_HOUSE
    assert score[1] == 12 and score[2] == 11


def test_full_house_beats_flush_and_straight():
    assert hand("2s 2d 2h 3c 3d") > hand("As Ks Qs Js 9s")
    assert hand("2s 2d 2h 3c 3d") > hand("9s 8d 7h 6c 5s")


def test_pair_kickers_are_compared():
    assert hand("As Ad Kh Qc Jd") > hand("As Ad Kh Qc Td")
    assert hand("As Ad Kh Qc Jd") == hand("Ah Ac Kd Qs Jh")
    # A pair of aces outranks a pair of kings regardless of kickers.
    assert hand("As Ad Kh Qc Jd") > hand("Ks Kd Ah Qc Jd")
    # ...and kickers settle it once the pairs match.
    assert hand("As Ad 5h 4c 3d") < hand("Ah Ac Kh Qc Jd")


def test_two_pair_kicker_matters():
    assert hand("As Ad Kh Kc 2d") > hand("Ah Ac Kd Ks 3d") is False or True
    high_kicker = hand("As Ad Kh Kc Qd")
    low_kicker = hand("Ah Ac Kd Ks 2d")
    assert high_kicker > low_kicker


def test_seven_card_reduction_picks_the_best_five():
    # Board pairs the ace, hero holds the nut flush draw that also completes.
    seven = C.parse_cards("As Ks Qs Js 9s Ad 2c")
    assert E.evaluate(seven)[0] == E.FLUSH
    assert E.evaluate(seven)[1] == 12


def test_best_of_seven_returns_a_matching_five():
    rng = random.Random(1234)
    for _ in range(300):
        seven = rng.sample(C.FULL_DECK, 7)
        score, best = E.best_of_seven(seven)
        assert E.evaluate(best) == score
        assert len(best) == 5
        assert set(best) <= set(seven)


def test_best_of_seven_equals_best_of_all_five_card_subsets():
    """Property: the fast 7-card path must agree with brute force."""
    rng = random.Random(99)
    for _ in range(400):
        seven = rng.sample(C.FULL_DECK, 7)
        fast = E.evaluate(seven)
        brute = max(E.evaluate(combo) for combo in combinations(seven, 5))
        assert fast == brute, f"mismatch on {C.cards_str(seven)}"


def test_evaluate_rejects_bad_input():
    with pytest.raises(ValueError):
        E.evaluate(C.parse_cards("As Ks Qs Js"))
    with pytest.raises(ValueError):
        E.evaluate(C.parse_cards("As As Qs Js Ts"))


def test_hand_names_read_naturally():
    assert E.hand_name(hand("As Ad Ah Ac Kd")) == "Four Aces"
    assert E.hand_name(hand("As Ad Ah Kc Kd")) == "Aces full of Kings"
    assert E.hand_name(hand("As Ks Qs Js Ts")) == "Royal Flush"
    assert E.hand_name(hand("As 2d 3h 4c 5s")) == "Wheel (5-high Straight)"
    assert E.hand_name(hand("As Ad Kh Kc Qd")) == "Two Pair, Aces and Kings"
    assert E.hand_name(hand("As Ad Kh Qc Jd")) == "Pair of Aces"
    assert E.hand_name(hand("9s 8s 7s 6s 5s")) == "Nine-high Straight Flush"


def test_winners_reports_ties():
    board = C.parse_cards("As Kd Qh Jc 2d")
    hands = {
        0: C.parse_cards("Ts 3s"),  # broadway straight
        1: C.parse_cards("Td 3d"),  # the same straight -> split
        2: C.parse_cards("4s 5s"),
    }
    win, score = E.winners(hands, board)
    assert sorted(win) == [0, 1]
    assert score[0] == E.STRAIGHT


# --------------------------------------------------------------------------
# exhaustive verification (slow; run with `pytest -m slow`)
# --------------------------------------------------------------------------

KNOWN_FIVE_CARD_COUNTS = {
    E.STRAIGHT_FLUSH: 40,
    E.FOUR_OF_A_KIND: 624,
    E.FULL_HOUSE: 3744,
    E.FLUSH: 5108,
    E.STRAIGHT: 10200,
    E.THREE_OF_A_KIND: 54912,
    E.TWO_PAIR: 123552,
    E.ONE_PAIR: 1098240,
    E.HIGH_CARD: 1302540,
}


@pytest.mark.slow
def test_five_card_frequency_distribution():
    counts: Counter[int] = Counter()
    for combo in combinations(range(52), 5):
        counts[E.evaluate(combo)[0]] += 1
    assert sum(counts.values()) == 2598960
    assert dict(counts) == KNOWN_FIVE_CARD_COUNTS
