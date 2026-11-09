"""The interface, driven headlessly.

Textual's test pilot lets these tests press real keys against a real app with a
throwaway config directory, so what is verified here is the thing the player
actually touches: the menu, the ladder, the action bar and the panels.

No audio, no network and no real mpv/cava/playerctl processes are involved.
"""

from __future__ import annotations

import asyncio

import pytest

from omapoker.data import DataStore, STARTING_CHIPS
from omapoker.engine.game import Street
from omapoker.session import GameSession, Mode
from omapoker.ui.app import OmaPokerApp
from omapoker.ui.screens.ladder import TablePickerScreen
from omapoker.ui.screens.menu import MainMenuScreen
from omapoker.ui.screens.panels import AssistScreen, GlossaryScreen, MusicScreen
from omapoker.ui.screens.table import BOARD, TableScreen

SIZE = (150, 46)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def make_store(tmp_path) -> DataStore:
    store = DataStore(tmp_path / "config")
    # The visualiser is off so no cava process is ever spawned by a test.
    store.save_settings({"visualiser": False, "cards_style": "ascii"})
    return store


def seated_app(tmp_path, *, players: int = 4):
    store = make_store(tmp_path)
    store.create_profile("Alice", "casual")
    app = OmaPokerApp(store=store, seed=1234)
    return app


def run(coro):
    return asyncio.run(coro)


def screen_text(screen) -> str:
    """All the text a screen is currently showing."""
    from textual.widgets import Static

    return "\n".join(str(widget.content) for widget in screen.query(Static))


async def sit_down(pilot, app) -> None:
    """Menu -> ladder -> table."""
    await pilot.press("p")
    await pilot.pause()
    assert isinstance(app.screen, TablePickerScreen)
    await pilot.press("enter")
    await pilot.pause()
    assert isinstance(app.screen, TableScreen)


async def play_until_complete(pilot, app, limit: int = 60) -> None:
    for _ in range(limit):
        session = app.session
        if session is None or session.hand is None:
            return
        if session.hand.street is Street.COMPLETE:
            return
        await pilot.press("c")
        await pilot.pause()


# --------------------------------------------------------------------------
# first run
# --------------------------------------------------------------------------

def test_first_run_asks_for_a_name_and_experience(tmp_path):
    store = make_store(tmp_path)
    app = OmaPokerApp(store=store, seed=1)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            from omapoker.ui.screens.onboarding import OnboardingScreen

            assert isinstance(app.screen, OnboardingScreen)
            # The name box already has focus, so type straight into it.
            await pilot.press(*"Alice")
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, MainMenuScreen)

    run(scenario())
    profiles = store.list_profiles()
    assert [p.name for p in profiles] == ["Alice"]
    assert store.bankroll_for(profiles[0].id).chips == STARTING_CHIPS


def test_menu_is_the_first_screen_with_a_profile(tmp_path):
    app = seated_app(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, MainMenuScreen)
            assert app.player is not None
            assert app.dial.level == 4  # a "casual" player starts at 4/5

    run(scenario())


@pytest.mark.parametrize("size", [(80, 24), (100, 34)])
def test_menu_buttons_remain_reachable_at_normal_terminal_sizes(tmp_path, size):
    from textual.widgets import Button

    app = seated_app(tmp_path)

    async def scenario():
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            if size[1] >= 34:
                assert all(button.region.bottom < size[1] for button in app.screen.query(Button))
            quit_button = app.screen.query_one("#btn-quit", Button)
            quit_button.focus()
            await pilot.pause(0.3)
            assert 0 <= quit_button.region.y < quit_button.region.bottom < size[1]

    run(scenario())


def test_app_seed_reproduces_the_deal(tmp_path):
    from omapoker.data.ladder import level_for

    def deal(root, seed):
        store = make_store(root)
        profile = store.create_profile("Alice", "casual")
        app = OmaPokerApp(store=store, seed=seed)
        app.load_profile(profile)
        app.open_table = lambda: None
        session = app.start_session(level_for(5_000))
        hand = session.start_hand()
        return session.table.button, hand.seed, [seat.hole for seat in session.seats]

    assert deal(tmp_path / "first", 1234) == deal(tmp_path / "second", 1234)
    assert deal(tmp_path / "third", 4321) != deal(tmp_path / "fourth", 1234)


# --------------------------------------------------------------------------
# playing
# --------------------------------------------------------------------------

def test_ladder_starts_a_table_and_a_hand(tmp_path):
    app = seated_app(tmp_path)
    state = {}

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await sit_down(pilot, app)
            session = app.session
            assert session is not None
            assert session.hand is not None
            assert len(session.seats) >= 2
            # The buy-in left the bankroll and became the stack at the table.
            assert app.player.bankroll.chips == STARTING_CHIPS - session.buy_in
            assert session.seats[0].stack > 0
            # The hero can always see their own two cards.
            assert len(session.visible_hole(0, 0)) == 2
            state["hand"] = session.hand.hand_id

    run(scenario())
    assert state["hand"] == 1


def test_the_rendered_seat_shows_both_hole_cards(tmp_path):
    from rich.text import Text
    from textual.geometry import Region
    from omapoker.ui import theme

    app = seated_app(tmp_path)

    async def scenario():
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            await sit_down(pilot, app)
            seat = app.screen.query_one("#seat-0")
            visible = "\n".join(
                strip.text for strip in seat.render_lines(
                    Region(0, 0, seat.region.width, seat.region.height)
                )
            )
            style = theme.cards_style(app.player.profile.cards_style)
            assert all(
                Text.from_markup(theme.card_markup(card, style)).plain in visible
                for card in app.session.seats[0].hole
            )
            assert app.screen.query_one("#action-hint").region.bottom <= 34

    run(scenario())


def test_the_hand_can_be_played_with_keys_and_chips_are_conserved(tmp_path):
    app = seated_app(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await sit_down(pilot, app)
            session = app.session
            before = sum(s.stack for s in session.seats) + (
                session.hand.total_pot if session.hand else 0
            )
            await play_until_complete(pilot, app)
            assert session.hand.street is Street.COMPLETE
            after = sum(s.stack for s in session.seats) + (
                session.hand.total_pot if session.hand else 0
            )
            assert after == before
            assert app.screen._hand_over is True

    run(scenario())


def test_next_hand_key_deals_another_hand(tmp_path):
    app = seated_app(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await sit_down(pilot, app)
            await play_until_complete(pilot, app)
            first = app.session.hands_played
            await pilot.press("n")
            await pilot.pause()
            assert app.session.hands_played == first + 1
            assert app.session.hand is not None
            # The new hand is dealt and either running or already settled by
            # everyone folding to the blinds.
            assert app.session.hand.street is not None

    run(scenario())


def test_raise_mode_prefills_an_amount_and_submits_it(tmp_path):
    app = seated_app(tmp_path)
    seen = {}

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await sit_down(pilot, app)
            session = app.session
            # Fold around until it is the hero's turn (bots act instantly, so the
            # very first human turn should arrive on its own).
            for _ in range(30):
                if session.awaiting_human:
                    break
                await pilot.press("n")
                await pilot.pause()
            assert session.awaiting_human
            legal = session.legal_actions()
            stack_before = session.seats[0].stack
            await pilot.press("r")
            await pilot.pause()
            seen["pending"] = app.screen._pending
            if legal.can_raise or legal.can_bet:
                seen["amount"] = app.screen.query_one("ActionBar").amounts()
                await pilot.press("enter")
                await pilot.pause()
                # The engine recorded the aggression, whichever way the hand went.
                seen["last_action"] = session.seats[0].last_action
            else:
                await pilot.press("escape")

    run(scenario())
    assert seen["pending"] in ("raise", "bet")
    if "amount" in seen:
        assert seen["amount"] > 0
        assert seen["last_action"].startswith(("raise", "bet"))


def test_leaving_the_table_returns_the_stack_to_the_bankroll(tmp_path):
    app = seated_app(tmp_path)
    seen = {}

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await sit_down(pilot, app)
            await play_until_complete(pilot, app)
            session = app.session
            seen["stack"] = session.seats[0].stack
            seen["bankroll"] = app.player.bankroll.chips
            await pilot.press("q")
            await pilot.pause()
            assert app.session is None
            assert isinstance(app.screen, MainMenuScreen)

    run(scenario())
    assert app.player.bankroll.chips == seen["bankroll"] + seen["stack"]
    assert app.player.stats.hands == 1


def test_quitting_between_hands_cashes_out_the_table(tmp_path):
    app = seated_app(tmp_path)
    seen = {}

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await sit_down(pilot, app)
            await play_until_complete(pilot, app)
            seen["balance"] = app.player.bankroll.chips + app.session.seats[0].stack
            seen["profile"] = app.player.profile.id
            seen["hands"] = app.player.stats.hands
            await pilot.press("ctrl+q")

    run(scenario())
    assert app.session is None
    assert app.store.bankroll_for(seen["profile"]).chips == seen["balance"]
    assert app.store.stats_for(seen["profile"]).hands == seen["hands"]


def test_leaving_a_table_removes_the_picker_and_previous_menu(tmp_path):
    app = seated_app(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await sit_down(pilot, app)
            await play_until_complete(pilot, app)
            await pilot.press("q")
            await pilot.pause()
            assert isinstance(app.screen, MainMenuScreen)
            assert len(app.screen_stack) == 2  # Textual's base screen and the menu
            assert sum(isinstance(screen, MainMenuScreen) for screen in app.screen_stack) == 1
            assert not any(isinstance(screen, TablePickerScreen) for screen in app.screen_stack)

    run(scenario())


def test_assist_dial_can_be_changed_mid_hand(tmp_path):
    app = seated_app(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await sit_down(pilot, app)
            await pilot.press("a")
            await pilot.pause()
            assert isinstance(app.screen, AssistScreen)
            await pilot.press("5")
            await pilot.pause()
            assert isinstance(app.screen, TableScreen)
            assert app.dial.level == 5
            assert app.player.profile.assist_level == 5

            await pilot.press("a")
            await pilot.pause()
            await pilot.press("0")
            await pilot.pause()
            assert app.dial.level == 0
            assert app.player.profile.assist_level == 0
            # The coach panel is mounted and the table is still live underneath.
            assert app.screen.query_one("CoachPanel") is not None
            assert app.session is not None

    run(scenario())


def test_assist_off_stays_off_when_the_profile_is_loaded_again(tmp_path):
    app = seated_app(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            app.set_assist(0)
            profile = app.store.load_profile(app.player.profile.id)
            app.load_profile(profile)
            assert app.dial.level == 0

    run(scenario())


def test_folding_is_always_available(tmp_path):
    app = seated_app(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await sit_down(pilot, app)
            session = app.session
            for _ in range(30):
                if session.awaiting_human:
                    break
                await pilot.press("n")
                await pilot.pause()
            assert session.awaiting_human
            await pilot.press("f")
            await pilot.pause()
            assert session.seats[0].folded is True

    run(scenario())


# --------------------------------------------------------------------------
# panels
# --------------------------------------------------------------------------

@pytest.mark.parametrize("key", ["s", "l", "t", "u", "m"])
def test_menu_panels_open(tmp_path, key):
    app = seated_app(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press(key)
            await pilot.pause()
            assert app.screen is not None
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, MainMenuScreen)

    run(scenario())


def test_glossary_searches(tmp_path):
    app = seated_app(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("?")
            await pilot.pause()
            assert isinstance(app.screen, GlossaryScreen)
            assert "Pot Odds" in screen_text(app.screen) or "pot odds" in screen_text(
                app.screen
            ).lower()
            app.screen.show_terms("pot odds")
            text = screen_text(app.screen)
            assert "pot odds" in text.lower()
            app.screen.show_terms("zzzz-not-a-term")
            assert "Nothing matched" in screen_text(app.screen)
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, MainMenuScreen)

    run(scenario())


def test_stats_screen_renders_numbers(tmp_path):
    app = seated_app(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await sit_down(pilot, app)
            await play_until_complete(pilot, app)
            await pilot.press("q")
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            text = screen_text(app.screen)
            assert "Rates" in text
            assert "Leaks" in text

    run(scenario())


def test_music_screen_lists_sources(tmp_path):
    app = seated_app(tmp_path)
    seen = {}

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("m")
            await pilot.pause()
            assert isinstance(app.screen, MusicScreen)
            seen["sources"] = [label for _source, label in app.music.source_labels()]
            seen["now"] = screen_text(app.screen)
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, MainMenuScreen)

    run(scenario())
    assert seen["sources"][0] == "Off"
    assert "OmaPoker Local" in seen["sources"]
    assert "Music is off" in seen["now"]


def test_the_header_cycles_music_and_missing_players_do_not_interrupt_poker(tmp_path):
    from omapoker.music import LOCAL, OFF

    app = seated_app(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            app.music.mpris.binary = "no-such-playerctl"
            app.music.player.binary = "no-such-mpv"
            app.music.library.set_folders([tmp_path / "empty-music"])
            await sit_down(pilot, app)
            table = app.screen

            await pilot.click("#music-source")
            assert app.screen is table
            assert app.music.source == LOCAL
            await pilot.pause(0.3)  # Let Textual release the button's pressed state.
            await pilot.click("#music-source")
            assert app.music.source == OFF
            await pilot.click("#music-chip")
            assert isinstance(app.screen, MusicScreen)
            await pilot.press("escape")
            assert app.screen is table
            await pilot.press("f")
            assert app.session.hand.street is Street.COMPLETE or app.session.seats[0].folded

    run(scenario())


def test_table_ring_gives_every_seat_a_place(tmp_path):
    app = seated_app(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await sit_down(pilot, app)
            session = app.session
            rows = app.screen._ring_rows(len(session.seats))
            placed = [
                cell
                for row in rows
                for cell in row
                if isinstance(cell, int)
            ]
            assert sorted(placed) == list(range(len(session.seats)))
            assert any(cell is BOARD for row in rows for cell in row)
            for seat in session.seats:
                assert app.screen.query_one(f"#seat-{seat.index}") is not None

            # The local player is the large bottom seat and the community
            # cards live on a visibly larger central felt.
            hero = app.screen.query_one("#seat-0")
            felt = app.screen.query_one("#poker-table")
            assert hero.has_class("hero-seat")
            assert hero.region.y > felt.region.y
            assert hero.region.width >= 38
            assert felt.region.width >= 48

    run(scenario())


# --------------------------------------------------------------------------
# hot-seat and guests
# --------------------------------------------------------------------------

def test_hotseat_cards_and_advice_stay_hidden_until_the_next_player_is_ready(tmp_path):
    from omapoker.data.ladder import level_for
    from omapoker.ui import theme
    from omapoker.ui.screens.base import PrivacyScreen

    app = seated_app(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            app.start_session(
                level_for(5_000), mode=Mode.HOTSEAT,
                human_names=["Alice", "Bob"], bot_count=0,
            )
            await pilot.pause()
            table = next(screen for screen in app.screen_stack if isinstance(screen, TableScreen))

            for _ in range(2):
                assert isinstance(app.screen, PrivacyScreen)
                assert table._viewer is None
                assert not table._may_act()
                style = theme.cards_style(app.player.profile.cards_style)
                for seat in app.session.seats:
                    text = str(table.query_one(f"#seat-{seat.index}").content)
                    assert all(theme.card_markup(card, style) not in text for card in seat.hole)
                assert "recommend" not in str(table.query_one("#coach-panel").content).lower()

                actor = app.session.active_human.index
                await pilot.press("enter")
                assert app.screen is table
                assert table._viewer == actor
                text = str(table.query_one(f"#seat-{actor}").content)
                assert all(
                    theme.card_markup(card, style) in text
                    for card in app.session.seats[actor].hole
                )
                await pilot.press("c")
                await pilot.pause()

    run(scenario())


def test_hotseat_guests_have_their_own_throwaway_bankrolls(tmp_path):
    app = seated_app(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            guests = app.guest_players(["Alice", "Bob", "Carol"], 5_000)
            assert [g.profile.name for g in guests] == ["Alice", "Bob", "Carol"]
            assert all(g.guest for g in guests[1:])
            assert all(g.bankroll.chips == 5_000 for g in guests[1:])

            from omapoker.data.ladder import level_for

            session = GameSession(
                app.player,
                level_for(5_000),
                store=app.store,
                human_names=["Alice", "Bob", "Carol"],
                human_players=guests,
                seed=99,
                mode=Mode.HOTSEAT,
            )
            assert len(session.human_seats) == 3
            # Every human paid their own buy-in.
            assert session.seats[0].stack == 5_000
            assert session.seats[1].stack == 5_000
            # A guest is never written to disk.
            assert not (app.store.root / "bankroll").joinpath("guest-1").exists()
            for index in session.human_seats:
                assert session.holder_for(index).guest == (index != 0)
            session.leave()

    run(scenario())


def test_ring_rows_shrink_for_small_tables():
    from omapoker.ui.screens.table import CLOCK

    app_screen_rows = TableScreen._ring_rows
    for count in range(2, 10):
        rows = app_screen_rows(None, count)  # type: ignore[arg-type]
        placed = [cell for row in rows for cell in row if isinstance(cell, int)]
        assert sorted(placed) == list(range(count))
    assert len(CLOCK) == 8
