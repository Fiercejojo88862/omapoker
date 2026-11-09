"""Chips left on a table by an unclean exit must come back.

A bankroll is the one number in OmaPoker that cannot be won back, so sitting down
is written to disk before the first card is dealt. These tests cover the note
itself (written, tracked, cleared), the recovery on the next start (paid once,
never twice), and the interface paths that decide whether a note is left behind.
"""

from __future__ import annotations

import pytest

from omapoker.data import STARTING_CHIPS, DataStore
from omapoker.data.ladder import level_for
from omapoker.data.tables import TableSeat
from omapoker.engine.game import Street
from omapoker.session import GameSession

MICRO = level_for(5_000)


def make_store(tmp_path) -> DataStore:
    return DataStore(tmp_path / "config")


def make_session(tmp_path, *, level=MICRO, **kwargs):
    store = make_store(tmp_path)
    profile = store.create_profile("Joseph", "casual")
    player = store.player(profile.id)
    session = GameSession(
        player,
        level,
        store=store,
        seed=5,
        auto_rebuy_bots=False,
        **kwargs,
    )
    return store, player, session


def play_one_hand(session: GameSession) -> None:
    session.start_hand()
    guard = 0
    while session.hand is not None and session.hand.street is not Street.COMPLETE:
        guard += 1
        assert guard < 500
        actor = session.actor()
        if actor is None:
            break
        if actor.is_human:
            legal = session.legal_actions()
            if legal.can_call:
                session.human_action("call", legal.call_amount)
            elif legal.can_check:
                session.human_action("check")
            elif legal.can_fold:
                session.human_action("fold")
            else:
                break
        else:
            session.run_bots()
    session.finish_hand()


# --------------------------------------------------------------------------
# the note on disk
# --------------------------------------------------------------------------

def test_sitting_down_writes_the_stack_down_before_a_card_is_dealt(tmp_path):
    store, player, session = make_session(tmp_path)
    seat = store.table_seat(player.profile.id)
    assert seat is not None
    assert seat.stack == MICRO.buy_in
    assert seat.buy_in == MICRO.buy_in
    assert seat.level == MICRO.name


def test_the_note_follows_the_stack_hand_by_hand(tmp_path):
    store, player, session = make_session(tmp_path)
    play_one_hand(session)
    seat = store.table_seat(player.profile.id)
    assert seat is not None
    assert seat.stack == session.seats[0].stack


def test_leaving_clears_the_note(tmp_path):
    store, player, session = make_session(tmp_path)
    session.leave()
    assert store.table_seat(player.profile.id) is None


def test_a_rebuy_is_written_down_too(tmp_path):
    store, player, session = make_session(tmp_path)
    session.seats[0].stack = 0
    session.rebuy(0)
    seat = store.table_seat(player.profile.id)
    assert seat is not None
    assert seat.stack == MICRO.buy_in


# --------------------------------------------------------------------------
# recovery
# --------------------------------------------------------------------------

def test_a_kill_does_not_lose_the_stack(tmp_path):
    store, player, session = make_session(tmp_path)
    play_one_hand(session)
    stack = session.seats[0].stack
    # The process dies here: no leave(), no cash-in, nothing.
    del session

    player = store.player(player.profile.id)
    assert player.bankroll.chips == STARTING_CHIPS - MICRO.buy_in
    recovered = store.recover_table_seat(player)
    assert recovered == (stack, MICRO.name)
    assert player.bankroll.chips == STARTING_CHIPS - MICRO.buy_in + stack
    assert store.table_seat(player.profile.id) is None


def test_recovery_is_paid_once_even_if_it_is_interrupted(tmp_path):
    store, player, session = make_session(tmp_path)
    seat = store.table_seat(player.profile.id)
    assert seat is not None
    # A recovery that writes the bankroll but dies before clearing the note.
    player.bankroll.cash_out(seat.stack, f"recovered {seat.level} (sitting {seat.sit_id})")
    store.bankroll_store.save_bankroll(player.bankroll)

    player = store.player(player.profile.id)
    before = player.bankroll.chips
    assert store.recover_table_seat(player) is None
    assert player.bankroll.chips == before
    assert store.table_seat(player.profile.id) is None


def test_a_note_with_nothing_on_it_is_just_cleared(tmp_path):
    store, player, _session = make_session(tmp_path)
    store.save_table_seat(
        TableSeat(profile_id=player.profile.id, level=MICRO.name, stack=0)
    )
    player = store.player(player.profile.id)
    before = player.bankroll.chips
    assert store.recover_table_seat(player) is None
    assert player.bankroll.chips == before
    assert store.table_seat(player.profile.id) is None


def test_a_guest_at_a_hot_seat_table_leaves_no_note(tmp_path):
    store = make_store(tmp_path)
    profile = store.create_profile("Joseph", "casual")
    host = store.player(profile.id)
    guest_store = make_store(tmp_path / "guests")
    guest_profile = guest_store.create_profile("Friend", "casual")
    guest = guest_store.player(guest_profile.id)
    guest.guest = True
    session = GameSession(
        guest, MICRO, store=store, seed=5, auto_rebuy_bots=False
    )
    assert store.table_seat(guest_profile.id) is None
    session.leave()


def test_a_networked_client_leaves_no_note(tmp_path):
    store, player, _session = make_session(tmp_path)
    store.clear_table_seat(player.profile.id)
    from omapoker.session import Mode

    session = GameSession(
        player,
        MICRO,
        store=store,
        seed=5,
        auto_rebuy_bots=False,
        mode=Mode.CLIENT,
    )
    assert store.table_seat(player.profile.id) is None
    session.leave()


# --------------------------------------------------------------------------
# interface paths
# --------------------------------------------------------------------------

def test_quitting_the_app_with_a_live_table_cashes_in(tmp_path):
    from omapoker.ui.app import OmaPokerApp

    store = make_store(tmp_path)
    profile = store.create_profile("Joseph", "casual")
    store.update_setting("visualiser", False)
    store.update_setting("cards_style", "ascii")
    store.update_setting("lan_port", 0)

    app = OmaPokerApp(store=store)
    app.settings = store.settings()

    import asyncio

    async def scenario() -> int:
        async with app.run_test() as pilot:
            await pilot.pause()
            session = app.start_session(MICRO)
            await pilot.pause()
            stack = session.seats[0].stack
            # The way a terminal window closes: no quit key, no confirm.
            app.exit()
            await pilot.pause()
            return stack

    stack = asyncio.run(scenario())
    reloaded = store.bankroll_for(profile.id)
    assert reloaded.chips == STARTING_CHIPS - MICRO.buy_in + stack
    assert reloaded.ledger[-1].kind != "buy_in"
    assert store.table_seat(profile.id) is None


def test_ctrl_c_at_the_table_cashes_in(tmp_path):
    from omapoker.ui.app import OmaPokerApp

    store = make_store(tmp_path)
    profile = store.create_profile("Joseph", "casual")
    store.update_setting("visualiser", False)
    store.update_setting("lan_port", 0)

    app = OmaPokerApp(store=store)
    app.settings = store.settings()

    import asyncio

    async def scenario() -> int:
        async with app.run_test() as pilot:
            await pilot.pause()
            session = app.start_session(MICRO)
            await pilot.pause()
            stack = session.seats[0].stack
            await pilot.press("ctrl+c")
            await pilot.pause()
            return stack

    stack = asyncio.run(scenario())
    reloaded = store.bankroll_for(profile.id)
    assert reloaded.chips == STARTING_CHIPS - MICRO.buy_in + stack
    assert store.table_seat(profile.id) is None


def test_an_empty_bankroll_offers_a_way_back_in(tmp_path):
    from textual.widgets import Button, ListView

    from omapoker.ui.app import OmaPokerApp
    from omapoker.ui.screens.ladder import TablePickerScreen

    store = make_store(tmp_path)
    profile = store.create_profile("Joseph", "casual")
    store.update_setting("visualiser", False)
    store.update_setting("lan_port", 0)
    player = store.player(profile.id)
    player.bankroll.buy_in(STARTING_CHIPS, "everything")
    store.bankroll_store.save_bankroll(player.bankroll)

    app = OmaPokerApp(store=store)
    app.settings = store.settings()

    import asyncio

    async def scenario() -> int:
        async with app.run_test() as pilot:
            app.open_menu()
            await pilot.press("p")
            await pilot.pause()
            assert isinstance(app.screen, TablePickerScreen)
            view = app.screen.query_one("#ladder-list", ListView)
            assert all(item.disabled for item in view.children)
            await pilot.click("#rescue")
            await pilot.pause()
            assert app.player.bankroll.chips == STARTING_CHIPS
            assert isinstance(app.screen, TablePickerScreen)
            view = app.screen.query_one("#ladder-list", ListView)
            return sum(1 for item in view.children if not item.disabled)

    affordable = asyncio.run(scenario())
    assert affordable > 0
    assert store.table_seat(profile.id) is None


def test_the_next_start_recovers_a_stack_left_behind(tmp_path):
    from omapoker.ui.app import OmaPokerApp

    store = make_store(tmp_path)
    profile = store.create_profile("Joseph", "casual")
    store.update_setting("visualiser", False)
    store.update_setting("cards_style", "ascii")
    store.update_setting("lan_port", 0)
    store.set_active_profile(profile.id)

    player = store.player(profile.id)
    player.bankroll.buy_in(MICRO.buy_in, f"buy-in {MICRO.name}")
    store.bankroll_store.save_bankroll(player.bankroll)
    store.save_table_seat(
        TableSeat(profile_id=profile.id, level=MICRO.name, stack=MICRO.buy_in - 500)
    )

    app = OmaPokerApp(store=store)

    async def scenario() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()

    import asyncio

    asyncio.run(scenario())
    reloaded = store.bankroll_for(profile.id)
    assert reloaded.chips == STARTING_CHIPS - 500
    assert store.table_seat(profile.id) is None


def test_resetting_the_bankroll_clears_an_old_note(tmp_path):
    from omapoker.ui.app import OmaPokerApp

    store = make_store(tmp_path)
    profile = store.create_profile("Joseph", "casual")
    store.update_setting("visualiser", False)
    store.update_setting("lan_port", 0)
    store.set_active_profile(profile.id)
    store.save_table_seat(
        TableSeat(profile_id=profile.id, level=MICRO.name, stack=50_000)
    )

    app = OmaPokerApp(store=store)

    async def scenario() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.reset_bankroll()
            await pilot.pause()

    import asyncio

    asyncio.run(scenario())
    assert store.bankroll_for(profile.id).chips == STARTING_CHIPS
    assert store.table_seat(profile.id) is None


def test_deleting_a_profile_takes_its_note_with_it(tmp_path):
    store = make_store(tmp_path)
    profile = store.create_profile("Joseph", "casual")
    store.save_table_seat(
        TableSeat(profile_id=profile.id, level=MICRO.name, stack=1_000)
    )
    store.delete_profile(profile.id)
    assert store.table_seat(profile.id) is None
