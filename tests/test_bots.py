"""Bots must be legal, terminating and reasonably quick.

The integration test here is the real one: it seats bots at a full table and
plays many hands, asserting that every action is accepted, every hand finishes,
and the chips never change. A bot that produces an illegal action or a hand that
never ends shows up immediately.
"""

from __future__ import annotations

import random
import time

import pytest

from omapoker.engine import cards as C
from omapoker.engine.bots import (
    PROFILES,
    act_for,
    decide,
    make_bot,
    position_score,
    preflop_strength,
    profile_for,
    suggest_bot_names,
)
from omapoker.engine.game import Action, Hand, IllegalAction, Seat, Street, Table


def holes(text: str) -> list[int]:
    return C.parse_cards(text)


# --------------------------------------------------------------------------
# hand strength
# --------------------------------------------------------------------------

def test_preflop_strength_orders_hands_sensibly():
    aa = preflop_strength(holes("As Ah"))
    kk = preflop_strength(holes("Ks Kh"))
    aks = preflop_strength(holes("As Ks"))
    ako = preflop_strength(holes("As Kd"))
    trash = preflop_strength(holes("7c 2d"))
    assert aa > kk > aks > ako > trash
    assert 0.0 <= trash <= 1.0 and 0.0 <= aa <= 1.0


def test_suited_beats_offsuit_and_connected_beats_gapped():
    assert preflop_strength(holes("Js Ts")) > preflop_strength(holes("Js Td"))
    assert preflop_strength(holes("Js Ts")) > preflop_strength(holes("Js 9d"))


def test_position_score_ranks_the_button_last():
    seats = [make_bot(i, f"B{i}", 1000, "medium") for i in range(4)]
    hand = Hand(seats, button=0, small_blind=25, big_blind=50, seed=b"pos")
    hand.start()
    scores = {i: position_score(hand, i) for i in range(4)}
    # Postflop the order is seat 1, 2, 3, 0, so seat 0 is last to act.
    assert scores[0] == 1.0
    assert scores[1] == 0.0
    assert scores[0] > scores[2] > scores[1]


def test_profiles_are_distinct():
    assert PROFILES["easy"].mistake_rate > PROFILES["pro"].mistake_rate
    assert PROFILES["pro"].iterations > PROFILES["easy"].iterations
    assert PROFILES["pro"].bluff_frequency > PROFILES["easy"].bluff_frequency
    assert profile_for("nonsense").level == "medium"


def test_bot_names_avoid_collisions():
    names = suggest_bot_names(3, taken=["Ada"])
    assert "Ada" not in names
    assert len(names) == 3


# --------------------------------------------------------------------------
# always legal
# --------------------------------------------------------------------------

@pytest.mark.parametrize("level", ["easy", "medium", "hard", "pro"])
def test_bots_only_return_legal_actions(level):
    # A fixed seed: `hash(str)` varies per process, which made this test flaky.
    rng = random.Random(sum(ord(c) for c in level))
    profile = profile_for(level)
    checked = 0
    for _ in range(25):
        n = rng.randint(2, 6)
        seats = [make_bot(i, f"B{i}", rng.choice([200, 1000, 5000]), level) for i in range(n)]
        hand = Hand(
            seats, rng.randrange(n), rng.choice([10, 25, 50]), rng.choice([20, 50, 100]),
            seed=rng.randbytes(16),
        )
        hand.start()
        guard = 0
        while hand.street is not Street.COMPLETE and guard < 400:
            guard += 1
            actor = hand.to_act
            legal = hand.legal_actions()
            action, amount = decide(hand, actor, profile, rng)
            # The returned action must satisfy the engine's own rules.
            if action is Action.CHECK:
                assert legal.can_check
            elif action is Action.CALL:
                assert legal.can_call
            elif action is Action.FOLD:
                assert legal.can_fold
            elif action is Action.BET:
                assert legal.can_bet
                assert legal.min_bet <= amount <= legal.max_raise_to
            elif action is Action.RAISE:
                assert legal.can_raise
                assert amount == legal.max_raise_to or (
                    legal.min_raise_to <= amount <= legal.max_raise_to
                )
            act_for(hand, actor, profile, rng)
            checked += 1
        assert hand.street is Street.COMPLETE
    assert checked > 100


def test_a_bot_never_stalls_a_heads_up_hand():
    rng = random.Random(11)
    profile = profile_for("medium")
    for _ in range(20):
        seats = [make_bot(0, "A", 2000, "medium"), make_bot(1, "B", 2000, "medium")]
        hand = Hand(seats, 0, 25, 50, seed=rng.randbytes(16))
        hand.start()
        guard = 0
        while hand.street is not Street.COMPLETE:
            guard += 1
            assert guard < 200
            act_for(hand, hand.to_act, profile, rng)


# --------------------------------------------------------------------------
# full-table integration
# --------------------------------------------------------------------------

def test_bot_table_plays_many_hands_and_conserves_chips():
    rng = random.Random(2024)
    levels = ["easy", "medium", "hard", "pro"]
    seats = [
        make_bot(i, f"Bot{i}", 2000, levels[i % len(levels)]) for i in range(6)
    ]
    table = Table(seats, small_blind=10, big_blind=20)
    starting = sum(s.stack for s in seats)

    started = time.perf_counter()
    for _ in range(25):
        hand = table.start_hand(seed=rng.randbytes(16))
        guard = 0
        while hand.street is not Street.COMPLETE:
            guard += 1
            assert guard < 300, "a hand failed to terminate"
            actor = hand.to_act
            profile = profile_for(hand.seats[actor].level)
            act_for(hand, actor, profile, rng)
        assert table.chips_in_play() == starting
        table.finish_hand()
        # Keep the table alive: re-seat anyone who busted, as the real game does.
        for seat in seats:
            if seat.stack == 0:
                seat.stack = 2000
        starting = sum(s.stack for s in seats)
        table.advance_button()

    elapsed = time.perf_counter() - started
    assert elapsed < 20, f"25 hands took {elapsed:.1f}s; too slow for a UI"


def test_easy_bots_are_looser_than_pro_bots():
    """A behavioural check that difficulty really changes how they play.

    Absolute action counts are a trap here: tight bots fold early, so hands are
    shorter and they simply make fewer decisions. Compare shares instead.
    """
    rng = random.Random(7)
    counts: dict[str, dict[str, int]] = {
        "easy": {"call": 0, "fold": 0, "raise": 0},
        "pro": {"call": 0, "fold": 0, "raise": 0},
    }
    for level in counts:
        profile = profile_for(level)
        for _ in range(40):
            seats = [make_bot(i, f"B{i}", 1000, level) for i in range(3)]
            hand = Hand(seats, 0, 25, 50, seed=rng.randbytes(16))
            hand.start()
            guard = 0
            while hand.street is not Street.COMPLETE and guard < 200:
                guard += 1
                actor = hand.to_act
                action, _amount = decide(hand, actor, profile, rng)
                if action is Action.FOLD:
                    counts[level]["fold"] += 1
                elif action in (Action.BET, Action.RAISE):
                    counts[level]["raise"] += 1
                else:
                    counts[level]["call"] += 1
                act_for(hand, actor, profile, rng)

    easy, pro = counts["easy"], counts["pro"]
    easy_total = sum(easy.values())
    pro_total = sum(pro.values())
    assert easy_total and pro_total
    easy_fold_share = easy["fold"] / easy_total
    pro_fold_share = pro["fold"] / pro_total
    # The easy bot is a calling station: it folds far less often.
    assert easy_fold_share < pro_fold_share, (counts, easy_fold_share, pro_fold_share)
    # And it is the more passive of the two.
    assert easy["raise"] / easy_total <= pro["raise"] / pro_total
