"""Betting, side pots and chip conservation.

The centrepiece is :func:`test_chip_conservation_over_random_hands`, which plays
thousands of random hands and asserts that the chips on the table never change.
Any error in betting, side-pot construction or payout shows up as chips created
or destroyed, so this one test covers a very large surface.
"""

from __future__ import annotations

import random

import pytest

from omapoker.engine import cards as C
from omapoker.engine.game import (
    Action,
    Hand,
    IllegalAction,
    Seat,
    Street,
    Table,
)


def make_seats(stacks, names=None):
    names = names or [f"P{i}" for i in range(len(stacks))]
    return [Seat(i, names[i], stacks[i]) for i in range(len(stacks))]


def play_hand(stacks, button, sb=25, bb=50, actions=None):
    """Drive a hand with a scripted list of (action, amount) tuples."""
    seats = make_seats(stacks)
    hand = Hand(seats, button, sb, bb, seed=b"deterministic-seed")
    hand.start()
    for step in actions or []:
        act, amount = step
        hand.act(act, amount)
    return hand


# --------------------------------------------------------------------------
# blinds and action order
# --------------------------------------------------------------------------

def test_heads_up_blinds_and_order():
    hand = play_hand([1000, 1000], button=0)
    # Button posts the small blind and acts first preflop.
    assert hand.seats[0].committed_street == 25
    assert hand.seats[1].committed_street == 50
    assert hand.to_act == 0


def test_three_handed_blinds_and_order():
    hand = play_hand([1000, 1000, 1000], button=0)
    assert hand.seats[1].committed_street == 25  # sb
    assert hand.seats[2].committed_street == 50  # bb
    assert hand.to_act == 0  # utg, which is the button three-handed


def test_big_blind_gets_the_option():
    hand = play_hand([1000, 1000, 1000], button=0)
    hand.act(Action.CALL, 50)   # button
    hand.act(Action.CALL, 50)   # sb
    assert hand.to_act == 2, "big blind must still get to act"
    assert hand.legal_actions().can_check


def test_postflop_order_is_left_of_button():
    hand = play_hand([1000, 1000, 1000], button=0)
    hand.act(Action.CALL, 50)
    hand.act(Action.CALL, 50)
    hand.act(Action.CHECK)
    assert hand.street is Street.FLOP
    assert len(hand.board) == 3
    assert hand.to_act == 1  # small blind acts first after the flop


# --------------------------------------------------------------------------
# basic resolutions
# --------------------------------------------------------------------------

def test_fold_out_wins_the_pot():
    hand = play_hand([1000, 1000, 1000], button=0)
    hand.act(Action.FOLD)  # button folds
    hand.act(Action.FOLD)  # sb folds
    assert hand.result is not None
    assert hand.result.payouts[0].seat == 2
    # BB collects both blinds.
    assert hand.seats[2].stack == 1000 + 25


def test_chips_move_to_the_winner():
    starting = 3000
    hand = play_hand([1000, 1000, 1000], button=0)
    hand.act(Action.CALL, 50)
    hand.act(Action.FOLD)
    hand.act(Action.CHECK)
    assert hand.street is Street.FLOP
    hand.act(Action.BET, 100)  # sb bets
    hand.act(Action.FOLD)      # bb folds
    assert hand.result is not None
    assert sum(s.stack for s in hand.seats) == starting


def test_call_and_check_to_showdown():
    hand = play_hand([1000, 1000], button=0)
    hand.act(Action.CALL, 50)
    hand.act(Action.CHECK)
    for _ in range(3):
        assert hand.street in (Street.FLOP, Street.TURN, Street.RIVER)
        hand.act(Action.CHECK)
        hand.act(Action.CHECK)
    assert hand.street is Street.COMPLETE
    assert hand.result is not None and hand.result.showdown
    # Heads-up pot of 100 with no further betting: the winner is paid 100.
    assert sum(s.stack for s in hand.seats) == 2000
    assert [p.amount for p in hand.result.payouts] == [100]


# --------------------------------------------------------------------------
# raise rules
# --------------------------------------------------------------------------

def test_min_raise_is_enforced():
    hand = play_hand([1000, 1000, 1000], button=0)
    legal = hand.legal_actions()
    assert legal.min_raise_to == 100  # bb 50 + one raise of 50
    with pytest.raises(IllegalAction):
        hand.act(Action.RAISE, 75)
    hand.act(Action.RAISE, 100)
    assert hand.current_bet == 100


def test_raise_must_match_the_last_increment():
    hand = play_hand([1000, 1000, 1000], button=0)
    hand.act(Action.RAISE, 100)          # raise by 50
    hand.act(Action.RAISE, 150)          # raise by 50 -> min for next is +50
    legal = hand.legal_actions()
    assert legal.min_raise_to == 200, legal.describe()
    with pytest.raises(IllegalAction):
        hand.act(Action.RAISE, 175)


def test_cannot_check_facing_a_bet():
    hand = play_hand([1000, 1000, 1000], button=0)
    with pytest.raises(IllegalAction):
        hand.act(Action.CHECK)


def test_all_in_short_of_a_full_raise_does_not_reopen_betting():
    # Short stack shoves for less than a legal raise; the caller may not re-raise.
    seats = make_seats([1000, 1000, 120])
    hand = Hand(seats, button=0, small_blind=25, big_blind=50, seed=b"x")
    hand.start()
    hand.act(Action.RAISE, 100)   # button raises to 100
    hand.act(Action.FOLD)         # sb folds
    # BB has 120 total, so an all-in is to 120: a raise of only 20 over 100.
    assert hand.seats[2].stack == 70
    legal_bb = hand.legal_actions(2)
    assert legal_bb.max_raise_to == 120
    hand.act(Action.RAISE, 120)   # all in for less than a full raise
    # Button already acted, so raising is closed; only call or fold remain.
    legal = hand.legal_actions(0)
    assert legal.can_call and not legal.can_raise
    assert legal.call_amount == 20
    hand.act(Action.CALL, 20)
    # Both players are all in for 120, so the board runs out and the hand is
    # complete straight away.
    assert hand.street is Street.COMPLETE
    assert len(hand.board) == 5
    assert sum(s.stack for s in hand.seats) == 2120


def test_full_raise_reopens_the_betting():
    hand = play_hand([1000, 1000, 1000], button=0)
    hand.act(Action.RAISE, 100)
    hand.act(Action.RAISE, 150)  # full raise (50 on top of 50)
    legal = hand.legal_actions(0)
    assert legal.can_raise


def test_all_in_call_for_less():
    seats = make_seats([1000, 1000, 60])
    hand = Hand(seats, button=0, small_blind=25, big_blind=50, seed=b"x")
    hand.start()
    # Seat 2 is the big blind: 50 posted, 10 behind.
    assert hand.seats[2].committed_street == 50
    assert hand.seats[2].stack == 10
    hand.act(Action.RAISE, 200)   # button
    hand.act(Action.FOLD)         # small blind
    legal = hand.legal_actions(2)
    assert legal.call_amount == 10, "the short stack can only call what it has"
    hand.act(Action.CALL, 0)
    assert hand.seats[2].all_in
    assert hand.seats[2].stack == 0
    assert hand.seats[2].committed_total == 60


# --------------------------------------------------------------------------
# side pots
# --------------------------------------------------------------------------

def test_side_pots_with_three_way_all_ins():
    seats = make_seats([100, 500, 1000])
    hand = Hand(seats, button=0, small_blind=25, big_blind=50, seed=b"side")
    hand.start()
    # button=0, so seat 1 has the small blind and seat 2 the big blind.
    hand.act(Action.RAISE, 100)   # seat 0 all in for 100
    hand.act(Action.RAISE, 500)   # seat 1 all in for 500
    hand.act(Action.CALL, 450)    # seat 2 calls the extra 450
    assert hand.result is not None, "the hand must reach a showdown"
    pots = hand.build_pots()
    assert [p.amount for p in pots] == [300, 800]
    assert pots[0].eligible == [0, 1, 2]
    assert pots[1].eligible == [1, 2]
    assert sum(p.amount for p in pots) == 1100
    assert sum(s.stack for s in hand.seats) == 1600


def test_uncalled_bet_is_returned_via_top_side_pot():
    seats = make_seats([1000, 300])
    hand = Hand(seats, button=0, small_blind=25, big_blind=50, seed=b"uncalled")
    hand.start()
    hand.act(Action.RAISE, 1000)  # button shoves
    hand.act(Action.CALL, 300)    # bb can only call 300 total
    assert hand.result is not None
    assert sum(s.stack for s in hand.seats) == 1300
    pots = hand.build_pots()
    assert [p.amount for p in pots] == [600, 700]
    assert pots[1].eligible == [0], "the uncalled 700 comes back to the shover"


def test_split_pot_odd_chip_goes_left_of_the_button():
    # Force a chopped pot: the broadway board plays for all three players.
    seats = make_seats([1000, 1000, 1000])
    # Dealing starts left of the button (seat 1, seat 2, seat 0) and goes around
    # twice, with a burn card before each street.
    board = C.parse_cards("As Kd Qh Jc Td")
    rigged = (
        C.parse_cards("4h 6c 2c")    # first card: seats 1, 2, 0
        + C.parse_cards("5s 7d 3d")  # second card: seats 1, 2, 0
        + C.parse_cards("2h")        # burn
        + board[0:3]
        + C.parse_cards("2d")        # burn
        + board[3:4]
        + C.parse_cards("2s")        # burn
        + board[4:5]
    )
    assert len(rigged) == 14
    rest = [c for c in C.FULL_DECK if c not in rigged]
    hand = Hand(seats, button=0, small_blind=25, big_blind=50, deck=rigged + rest)
    hand.start()
    assert hand.seats[0].hole == C.parse_cards("2c 3d")
    assert hand.seats[1].hole == C.parse_cards("4h 5s")
    assert hand.seats[2].hole == C.parse_cards("6c 7d")

    while hand.street is not Street.COMPLETE:
        legal = hand.legal_actions()
        hand.act(
            Action.CHECK if legal.can_check else Action.CALL,
            legal.call_amount if legal.can_call else 0,
        )

    assert hand.board == board
    assert hand.result is not None and hand.result.showdown
    # All three chop the 150 pot: 50 apiece.
    assert sorted(p.seat for p in hand.result.payouts) == [0, 1, 2]
    assert all(p.amount == 50 for p in hand.result.payouts)
    assert sum(s.stack for s in hand.seats) == 3000


def test_odd_chip_in_a_split_goes_to_the_first_seat_left_of_the_button():
    # Four players, one of whom folds preflop after posting the small blind, so
    # the three-way chop is of a pot that is not divisible by three.
    seats = make_seats([1000, 1000, 1000, 1000])
    board = C.parse_cards("As Kd Qh Jc Td")
    rigged = (
        # Dealing order: seats 1, 2, 3, 0, then 1, 2, 3, 0.
        C.parse_cards("8h 4h 6c 2c")
        + C.parse_cards("9s 5s 7d 3d")
        + C.parse_cards("2h")        # burn
        + board[0:3]
        + C.parse_cards("2d")        # burn
        + board[3:4]
        + C.parse_cards("2s")        # burn
        + board[4:5]
    )
    rest = [c for c in C.FULL_DECK if c not in rigged]
    hand = Hand(seats, button=0, small_blind=25, big_blind=50, deck=rigged + rest)
    hand.start()
    # button 0 -> seat 1 posts 25, seat 2 posts 50; seat 3 is first to act.
    assert hand.to_act == 3
    hand.act(Action.CALL, 50)   # seat 3
    hand.act(Action.CALL, 50)   # seat 0
    hand.act(Action.FOLD)       # seat 1 folds, leaving 25 of dead money
    hand.act(Action.CHECK)      # seat 2

    while hand.street is not Street.COMPLETE:
        legal = hand.legal_actions()
        hand.act(
            Action.CHECK if legal.can_check else Action.CALL,
            legal.call_amount if legal.can_call else 0,
        )

    assert hand.result is not None
    # Pot is 25 (dead) + 50 + 50 + 50 = 175, chopped three ways. The pot is
    # awarded as two levels (the dead money level and the matched level), so
    # compare each seat's gross total.
    totals = hand.result.totals
    assert sorted(totals.values()) == [58, 58, 59], totals
    # The odd chip goes to the first winner left of the button: seat 2.
    assert [seat for seat, amount in totals.items() if amount == 59] == [2]
    assert sum(s.stack for s in hand.seats) == 4000


# --------------------------------------------------------------------------
# invariants
# --------------------------------------------------------------------------

def random_action(rng, hand):
    legal = hand.legal_actions()
    choices = ["fold", "check", "call", "bet", "raise"]
    weights = [1, 3 if legal.can_check else 0, 4 if legal.can_call else 0,
               3 if legal.can_bet else 0, 2 if legal.can_raise else 0]
    if sum(weights) == 0:
        return None
    act = rng.choices(choices, weights=weights, k=1)[0]
    if act == "check":
        return (Action.CHECK, 0)
    if act == "call":
        return (Action.CALL, 0)
    if act == "fold":
        return (Action.FOLD, 0)
    if act == "bet":
        span = legal.max_raise_to - legal.min_bet
        amount = legal.min_bet + (rng.randint(0, span) if span > 0 else 0)
        return (Action.BET, amount)
    span = legal.max_raise_to - legal.min_raise_to
    amount = legal.min_raise_to + (rng.randint(0, span) if span > 0 else 0)
    return (Action.RAISE, amount)


@pytest.mark.parametrize("seed", range(40))
def test_chip_conservation_over_random_hands(seed):
    rng = random.Random(seed)
    for _ in range(25):
        n = rng.randint(2, 6)
        stacks = [rng.choice([100, 250, 500, 1000, 2500]) for _ in range(n)]
        starting = sum(stacks)
        button = rng.randrange(n)
        seats = make_seats(stacks)
        hand = Hand(
            seats, button, rng.choice([10, 25, 50]), rng.choice([20, 50, 100]),
            seed=rng.randbytes(16),
        )
        hand.start()
        guard = 0
        while hand.street is not Street.COMPLETE:
            guard += 1
            assert guard < 500, "hand did not terminate"
            step = random_action(rng, hand)
            if step is None:
                break
            hand.act(*step)
            # The invariant holds after every single action.
            assert sum(s.stack for s in hand.seats) + hand.total_pot == starting
            assert all(s.stack >= 0 for s in hand.seats)
        assert hand.street is Street.COMPLETE, "hand must reach completion"
        assert sum(s.stack for s in hand.seats) == starting


def test_field_is_a_valid_deck_and_gets_revealed():
    hand = play_hand([1000, 1000], button=0)
    assert len(hand.original_deck) == 52
    assert len(set(hand.original_deck)) == 52
    assert len(hand.deck) == 52 - 4  # two players, two cards each
    reveal = hand.reveal_seed()
    assert reveal["deck"] == C.deck_key(hand.original_deck)


def test_hole_cards_are_dealt_from_the_top_two_each():
    hand = play_hand([1000, 1000, 1000], button=1)
    assert all(len(s.hole) == 2 for s in hand.seats)
    dealt = [c for s in hand.seats for c in s.hole]
    assert len(set(dealt)) == 6
    # Dealing starts left of the button and goes twice around.
    assert hand.seats[(1 + 1) % 3].hole[0] == hand.original_deck[0]
    assert hand.seats[(1 + 2) % 3].hole[0] == hand.original_deck[1]


def test_public_state_hides_other_players_cards():
    hand = play_hand([1000, 1000, 1000], button=0)
    state = hand.public_state(viewer=1)
    assert state["seats"][1]["hole"], "viewer sees their own cards"
    assert state["seats"][0]["hole"] == []
    assert state["seats"][0]["hole_hidden"] is True


# --------------------------------------------------------------------------
# table-level behaviour
# --------------------------------------------------------------------------

def test_table_rotates_button_and_tracks_chips():
    seats = make_seats([1000, 1000, 1000])
    table = Table(seats, small_blind=25, big_blind=50)
    for _ in range(6):
        hand = table.start_hand()
        for _ in range(50):
            if hand.street is Street.COMPLETE:
                break
            step = (Action.FOLD, 0)
            try:
                hand.act(*step)
            except IllegalAction:
                hand.act(Action.CHECK)
        assert table.chips_in_play() == 3000, "table chips are conserved"
        table.finish_hand()
        table.advance_button()
    assert table.hand_number == 6
    assert len(table.history) == 6
