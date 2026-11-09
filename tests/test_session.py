"""The session: engine plus bots plus bankroll, wired together.

This is the layer the interface drives, and the layer that had no test coverage
until the UI tests found a missing attribute in it. Everything here runs headless
and fast.
"""

from __future__ import annotations

import pytest

from omapoker.data import DataStore, STARTING_CHIPS
from omapoker.data.ladder import level_for
from omapoker.engine.game import IllegalAction, Street
from omapoker.session import GameSession, Mode

MICRO = level_for(5_000)


def make_store(tmp_path) -> DataStore:
    return DataStore(tmp_path / "config")


def make_session(tmp_path, *, level=MICRO, hands_bankroll=STARTING_CHIPS, **kwargs):
    store = make_store(tmp_path)
    profile = store.create_profile("Joseph", "casual")
    player = store.player(profile.id)
    player.bankroll.chips = hands_bankroll
    session = GameSession(
        player,
        level,
        store=store,
        seed=kwargs.pop("seed", 5),
        auto_rebuy_bots=kwargs.pop("auto_rebuy_bots", False),
        **kwargs,
    )
    return store, player, session


def play_one_hand(session: GameSession, *, call_only: bool = True) -> None:
    """Deal a hand and play it out, checking chips are conserved throughout."""
    session.start_hand()
    total = sum(s.stack for s in session.seats) + session.hand.total_pot
    guard = 0
    while session.hand.street is not Street.COMPLETE and guard < 300:
        guard += 1
        assert sum(s.stack for s in session.seats) + session.hand.total_pot == total
        actor = session.actor()
        if actor is None:
            break
        if actor.is_human:
            legal = session.legal_actions()
            if call_only and legal.can_check:
                session.human_action("check")
            elif call_only and legal.can_call:
                session.human_action("call", legal.call_amount)
            elif legal.can_fold:
                session.human_action("fold")
            else:
                break
        else:
            session.run_bots()
    assert session.hand.street is Street.COMPLETE
    session.finish_hand()


# --------------------------------------------------------------------------
# seating and bankroll
# --------------------------------------------------------------------------

def test_the_buy_in_leaves_the_bankroll_and_becomes_the_stack(tmp_path):
    store, player, session = make_session(tmp_path)
    assert session.buy_in == MICRO.buy_in
    assert player.bankroll.chips == STARTING_CHIPS - MICRO.buy_in
    assert session.seats[0].stack == MICRO.buy_in
    assert session.seats[0].is_human and not session.seats[0].is_bot
    # Everyone else at the table is a bot, and they all bought in too.
    bots = [s for s in session.seats if s.is_bot]
    assert bots and all(s.stack == MICRO.buy_in for s in bots)
    # The store recorded the withdrawal.
    reloaded = store.bankroll_for(player.profile.id)
    assert reloaded.chips == STARTING_CHIPS - MICRO.buy_in
    assert reloaded.ledger[-1].kind == "buy_in"


def test_leaving_cashes_the_stack_back_in(tmp_path):
    store, player, session = make_session(tmp_path)
    stack = session.seats[0].stack
    returned = session.leave()
    assert returned == stack
    assert player.bankroll.chips == STARTING_CHIPS
    assert store.bankroll_for(player.profile.id).chips == STARTING_CHIPS
    assert session.seats[0].stack == 0


def test_a_bankroll_too_small_for_the_table_buys_in_for_what_it_has(tmp_path):
    store, player, session = make_session(tmp_path, hands_bankroll=2_000)
    assert session.buy_in == 2_000
    assert session.seats[0].stack == 2_000
    assert player.bankroll.chips == 0


def test_rebuy_takes_chips_from_the_bankroll(tmp_path):
    store, player, session = make_session(tmp_path)
    session.seats[0].stack = 0
    taken = session.rebuy(0)
    assert taken == MICRO.buy_in
    assert session.seats[0].stack == MICRO.buy_in
    assert player.bankroll.chips == STARTING_CHIPS - 2 * MICRO.buy_in


def test_rebuy_is_only_for_human_seats(tmp_path):
    store, player, session = make_session(tmp_path)
    bot_seat = next(s.index for s in session.seats if s.is_bot)
    assert session.rebuy(bot_seat) == 0


# --------------------------------------------------------------------------
# playing hands
# --------------------------------------------------------------------------

def test_a_full_hand_can_be_played_and_chips_are_conserved(tmp_path):
    store, player, session = make_session(tmp_path)
    play_one_hand(session)
    assert session.hands_played == 1
    assert session.last_outcome is not None
    assert sum(s.stack for s in session.seats) + session.table.hand.total_pot == pytest.approx(
        len(session.seats) * MICRO.buy_in
    )


def test_many_hands_leave_the_chip_total_unchanged(tmp_path):
    store, player, session = make_session(tmp_path, seed=17)
    expected = len(session.seats) * MICRO.buy_in
    for _ in range(12):
        if session.seats[0].stack <= 0:
            session.rebuy(0)
            expected += MICRO.buy_in
        play_one_hand(session)
        assert sum(s.stack for s in session.seats) == expected
    assert player.stats.hands >= 12
    assert player.stats.hands >= player.stats.hands_won


def test_the_button_moves_and_no_seat_is_skipped(tmp_path):
    store, player, session = make_session(tmp_path)
    seen = set()
    for _ in range(10):
        if session.seats[0].stack <= 0:
            session.rebuy(0)
        play_one_hand(session)
        seen.add(session.table.button)
    assert len(seen) > 1


def test_bots_respond_immediately_after_a_human_acts(tmp_path):
    store, player, session = make_session(tmp_path)
    session.start_hand()
    guard = 0
    while not session.awaiting_human and guard < 50:
        guard += 1
        assert session.actor() is not None
        session.run_bots()
    assert session.awaiting_human
    session.human_action("call", session.legal_actions().call_amount)
    # human_action runs the bots, so the turn has either come back around or the
    # hand is over; either way no bot is left dangling.
    assert session.awaiting_human or session.hand.street is Street.COMPLETE


def test_stats_are_recorded_per_hand(tmp_path):
    store, player, session = make_session(tmp_path)
    play_one_hand(session)
    stats = player.stats
    assert stats.hands == 1
    assert stats.net_chips == session.last_outcome.net.get(0, 0)
    assert store.stats_for(player.profile.id).hands == 1


def test_finishing_a_hand_twice_keeps_one_record_and_one_button_rotation(tmp_path):
    store, player, session = make_session(tmp_path)
    play_one_hand(session)
    outcome = session.last_outcome
    button = session.table.button
    stacks = [seat.stack for seat in session.seats]

    assert session.finish_hand() is outcome
    assert player.stats.hands == 1
    assert store.stats_for(player.profile.id).hands == 1
    assert len(session.table.history) == 1
    assert session.table.button == button
    assert [seat.stack for seat in session.seats] == stacks

    play_one_hand(session)
    assert session.last_outcome.hand_id != outcome.hand_id
    assert player.stats.hands == 2
    assert len(session.table.history) == 2


@pytest.mark.parametrize(
    "humans,bots", [(1, 1), (1, 8), (2, 0), (2, 1), (3, 1), (6, 0), (6, 4), (9, 1)]
)
def test_humans_take_their_seats_before_bots_fill_the_table(tmp_path, humans, bots):
    store = make_store(tmp_path)
    players = [
        store.player(store.create_profile(f"Player {index}", "casual").id)
        for index in range(humans)
    ]
    names = [player.profile.name for player in players]
    session = GameSession(
        players[0], MICRO, store=store, mode=Mode.HOST,
        human_names=names, human_players=players, bot_count=bots, seed=5,
    )

    assert [seat.name for seat in session.seats if seat.is_human] == names
    assert session.human_seats == list(range(humans))
    assert len(session.seats) == min(9, humans + bots)
    assert all(session.holder_for(index) is players[index] for index in range(humans))


def test_the_outcome_net_matches_the_seat_stacks(tmp_path):
    store, player, session = make_session(tmp_path)
    before = [s.stack for s in session.seats]
    play_one_hand(session)
    outcome = session.last_outcome
    for seat in session.seats:
        assert seat.stack == before[seat.index] + outcome.net[seat.index]


# --------------------------------------------------------------------------
# visibility
# --------------------------------------------------------------------------

def test_hole_cards_are_only_visible_to_their_owner(tmp_path):
    store, player, session = make_session(tmp_path)
    session.start_hand()
    assert len(session.visible_hole(0, 0)) == 2
    assert session.visible_hole(1, 0) == []
    state = session.state(viewer=0)
    assert state["seats"][0]["hole"]
    other = [s for s in state["seats"] if s["index"] != 0 and s["hole"]]
    assert other == []


def test_a_showdown_reveals_the_cards_that_were_shown(tmp_path):
    store, player, session = make_session(tmp_path)
    play_one_hand(session)
    outcome = session.last_outcome
    for index, hole in outcome.reveal.items():
        assert session.visible_hole(index, None) == hole


def test_state_reports_the_table_shape(tmp_path):
    store, player, session = make_session(tmp_path)
    session.start_hand()
    state = session.state(viewer=0)
    for key in ("board", "pot", "street", "button", "small_blind", "big_blind", "seats"):
        assert key in state
    assert state["street"] == "preflop"
    assert len(state["seats"]) == len(session.seats)
    assert state["bankroll"] == player.bankroll.chips


# --------------------------------------------------------------------------
# hot-seat and remote seats
# --------------------------------------------------------------------------

def test_hotseat_seats_every_named_human(tmp_path):
    store = make_store(tmp_path)
    profile = store.create_profile("Joseph", "casual")
    player = store.player(profile.id)
    guests = []
    for position, name in enumerate(["Natalie", "Jeremiah"], start=1):
        guest_profile = store.create_profile(name, "casual")
        guests.append(store.player(guest_profile.id))
    session = GameSession(
        player,
        MICRO,
        store=store,
        human_names=["Joseph", "Natalie", "Jeremiah"],
        human_players=[player, *guests],
        seed=3,
        mode=Mode.HOTSEAT,
    )
    assert len(session.human_seats) == 3
    names = {index: session.seats[index].name for index in session.human_seats}
    assert set(names.values()) == {"Joseph", "Natalie", "Jeremiah"}
    # Each human paid their own way.
    assert player.bankroll.chips == STARTING_CHIPS - MICRO.buy_in


def test_apply_remote_only_accepts_the_active_seat(tmp_path):
    store, player, session = make_session(tmp_path)
    session.start_hand()
    guard = 0
    while not session.awaiting_human and guard < 50:
        guard += 1
        session.run_bots()
    assert session.awaiting_human
    with pytest.raises(IllegalAction):
        session.apply_remote(7, "check")
    legal = session.legal_actions()
    if legal.can_check:
        session.apply_remote(0, "check")
        assert session.hand.events[-1].kind == "check"


def test_a_disconnected_seat_can_be_handed_to_a_bot(tmp_path):
    store, player, session = make_session(tmp_path)
    bot_seat = next(s.index for s in session.seats if s.is_bot)
    seat = session.replace_seat_with_bot(bot_seat)
    assert seat.is_bot and not seat.is_human
    assert session.seat_for_profile(player.profile.id) is not None


def test_finishing_without_a_hand_is_harmless(tmp_path):
    store, player, session = make_session(tmp_path)
    assert session.finish_hand() is None
    assert session.state()["in_hand"] is False


def test_not_enough_players_raises_instead_of_dealing(tmp_path):
    store, player, session = make_session(tmp_path)
    for seat in session.seats[1:]:
        seat.stack = 0
    session.seats[0].stack = 0
    assert session.live is False
    with pytest.raises(RuntimeError):
        session.start_hand()
