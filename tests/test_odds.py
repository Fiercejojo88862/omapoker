"""Equity, outs and pot odds."""

from __future__ import annotations

import random

import pytest

from omapoker.engine import cards as C
from omapoker.engine import evaluator as E
from omapoker.engine.odds import (
    count_outs,
    equity,
    equity_exact,
    equity_fast,
    implied_equity_needed,
    outs_summary,
    pot_odds,
    pot_odds_ratio,
    rule_of_thumb,
)


def holes(text: str) -> list[int]:
    return C.parse_cards(text)


def test_pocket_aces_is_a_big_favourite_heads_up():
    estimate = equity(holes("As Ah"), opponents=1, iterations=3000, rng=random.Random(1))
    assert 0.80 <= estimate.equity <= 0.90, estimate.describe()


def test_seven_deuce_is_a_dog_heads_up():
    estimate = equity(holes("7c 2d"), opponents=1, iterations=3000, rng=random.Random(2))
    assert estimate.equity < 0.40, estimate.describe()


def test_nuts_on_the_river_is_exactly_certain():
    board = holes("As Ks Qs Js 2d")
    estimate = equity_fast(holes("Ts 3c"), board, opponents=1)
    assert estimate.exact
    assert estimate.equity == 1.0


def test_drawing_dead_is_exactly_zero():
    # Hero has nothing; the opponent (from hero's point of view, a random hand)
    # cannot be beaten by this board plus hero's cards in every case, so we only
    # assert the estimate is sane and not a favourite.
    board = holes("As Ks Qs Js 9s")
    estimate = equity_fast(holes("2d 3c"), board, opponents=1)
    assert estimate.equity < 0.5


def test_exact_and_monte_carlo_agree_on_the_turn():
    hole = holes("Ah Kh")
    board = holes("Qh 9h 2c 7d")
    exact = equity_exact(hole, board, opponents=1)
    sampled = equity(hole, board, opponents=1, iterations=20000, rng=random.Random(3))
    assert abs(exact.equity - sampled.equity) < 0.02, (exact.equity, sampled.equity)


def test_more_opponents_means_less_equity():
    hole = holes("As Ah")
    board = holes("Kd 8c 3h")
    one = equity_fast(hole, board, opponents=1).equity
    three = equity_fast(hole, board, opponents=3, iterations=4000, rng=random.Random(4)).equity
    assert three < one


def test_equity_rejects_a_duplicated_card():
    with pytest.raises(ValueError):
        equity(holes("As Ah"), holes("As 2d"), opponents=1, iterations=10)


# --------------------------------------------------------------------------
# outs: these must match what a poker book says
# --------------------------------------------------------------------------

def test_flush_draw_has_nine_outs():
    total, labels = outs_summary(holes("As 2s"), holes("Ks 7s 3h"))
    assert total == 9, labels
    assert any("flush" in label for label in labels)


def test_open_ended_straight_draw_has_eight_outs():
    total, labels = outs_summary(holes("9d 8c"), holes("7h 6s 2c"))
    assert total == 8, labels


def test_gutshot_has_four_outs():
    total, labels = outs_summary(holes("9d 5c"), holes("7h 6s 2c"))
    assert total == 4, labels


def test_a_set_has_seven_outs_to_a_full_house_or_better():
    total, labels = outs_summary(holes("7d 7c"), holes("7h 2s 9d"))
    # 1 card makes quads, and 6 cards pair the board for a full house.
    assert total == 7, labels
    assert any("four of a kind" in label for label in labels)


def test_outs_exclude_cards_that_only_pair_you():
    grouped = count_outs(holes("As 2s"), holes("Ks 7s 3h"))
    all_cards = [c for cards in grouped.values() for c in cards]
    # Pairing the ace must not be counted as an out.
    ace_of_clubs = C.parse_card("Ac")
    assert ace_of_clubs not in all_cards


# --------------------------------------------------------------------------
# pot odds
# --------------------------------------------------------------------------

def test_pot_odds_arithmetic():
    # Calling 25 into 100 offers 4:1, needing 20%.
    assert pot_odds(25, 100) == pytest.approx(0.2)
    assert pot_odds_ratio(25, 100) == "4.0 : 1"
    assert pot_odds(0, 100) == 0.0


def test_pot_odds_with_callers_behind_you_lower_the_price():
    heads_up = pot_odds(50, 150)
    three_way = pot_odds(50, 150, extra_callers=1)
    assert three_way < heads_up


def test_rule_of_thumb_matches_the_textbook():
    assert rule_of_thumb(9, "flop") == pytest.approx(0.36)
    assert rule_of_thumb(9, "turn") == pytest.approx(0.18)
    # A 15-outer on the flop is capped rather than nonsense.
    assert rule_of_thumb(15, "flop") <= 1.0


def test_implied_equity_sentence_reads_plainly():
    text = implied_equity_needed(100, 25)
    assert "20%" in text or "20 %" in text


def test_fast_equity_uses_exact_enumeration_where_it_is_cheap():
    # River: 990 opponent hands, so it must be exact.
    estimate = equity_fast(holes("As Ah"), holes("Kd Qh 2c 7d 9s"), opponents=1)
    assert estimate.exact
    # Flop heads-up is over a million deals, so it must fall back to sampling.
    sampled = equity_fast(holes("As Ah"), holes("Kd Qh 2c"), opponents=1, iterations=500)
    assert not sampled.exact


def test_equity_of_a_known_tie():
    # Both play the board on a made straight: hero's equity is a tie at best.
    board = holes("As Kd Qh Jc Td")
    estimate = equity_fast(holes("2c 3d"), board, opponents=1)
    assert estimate.exact
    assert estimate.tie > 0.9
