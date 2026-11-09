"""A real host or guest UI in its own process, driven by test_lan_process."""

from __future__ import annotations

import asyncio
import faulthandler
import json
import os
from pathlib import Path
import sys
import time

ROLE, ROOT = sys.argv[1], Path(sys.argv[2])
BASE = ROOT / ROLE
os.environ.update(
    OMAPOKER_CONFIG_DIR=str(BASE / "config"),
    OMAPOKER_STATE_DIR=str(BASE / "state"),
    OMAPOKER_SHARE_DIR=str(BASE / "share"),
    OMAPOKER_NO_NOTIFY="1",
)
faulthandler.dump_traceback_later(35, exit=True)

from textual.widgets import ListView, Select

from omapoker.data import DataStore, STARTING_CHIPS
from omapoker.engine import verify_deck
from omapoker.engine.game import Street
from omapoker.ui.app import OmaPokerApp
from omapoker.ui.screens.ladder import TablePickerScreen
from omapoker.ui.screens.menu import MainMenuScreen
from omapoker.ui.screens.net import HostScreen, HostTableScreen, JoinScreen, RemoteTableScreen

SIZE = (150, 46)
HANDS = 3


def checkpoint(text: str) -> None:
    # Textual captures stdout while mounted. Keep progress even if a peer hangs.
    with (ROOT / f"{ROLE}.progress").open("a") as log:
        log.write(text + "\n")


async def wait_for(predicate, description: str, timeout: float = 12.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, f"Timed out: {description}"
        await asyncio.sleep(0.05)


def make_app() -> OmaPokerApp:
    store = DataStore(BASE / "config")
    store.save_settings({"visualiser": False, "cards_style": "ascii", "lan_port": 0})
    store.create_profile("Host" if ROLE == "host" else "Guest", "casual")
    return OmaPokerApp(store=store, seed=11)


async def host() -> None:
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("n")
        assert isinstance(app.screen, HostScreen)
        server = app.host_server
        assert server is not None
        (ROOT / "invite.json").write_text(json.dumps({"port": server.port, "code": server.room_code}))
        checkpoint("lobby opened")
        await wait_for(lambda: any(p.verified for p in server.players()), "guest authentication")

        await pilot.press("s")
        assert isinstance(app.screen, TablePickerScreen)
        picker = app.screen
        picker.query_one("#ladder-list", ListView).index = 95  # 5,000-chip table
        picker.query_one("#opponents", Select).value = "1"
        await pilot.press("enter")
        await wait_for(lambda: isinstance(app.screen, HostTableScreen), "host table")
        screen = app.screen
        session = app.session
        assert len(session.seats) == 3  # Both humans, plus the requested bot.
        checkpoint("table started")

        for hand_id in range(1, HANDS + 1):
            deadline = time.monotonic() + 12.0
            while not screen._hand_over:
                assert time.monotonic() < deadline, f"Host stuck in hand {hand_id}"
                actor = session.active_human
                if actor is not None and actor.index == 0:
                    await pilot.press("c")
                await asyncio.sleep(0.05)
            assert session.hand.street is Street.COMPLETE
            checkpoint(f"hand {hand_id} completed")
            await wait_for(lambda: (ROOT / f"result-{hand_id}").exists(), "guest hand result")
            if hand_id < HANDS:
                await pilot.press("n")

        balance = app.player.bankroll.chips + session.seats[0].stack
        profile_id = app.player.profile.id
        await pilot.press("q")
        await pilot.pause()
        assert isinstance(app.screen, MainMenuScreen)
        assert app.session is None
        assert app.store.bankroll_for(profile_id).chips == balance
        assert app.store.stats_for(profile_id).hands == HANDS
        assert len(app.screen_stack) == 2
        await wait_for(lambda: (ROOT / "guest-left").exists(), "guest goodbye")
        checkpoint("host closed cleanly")


async def guest() -> None:
    await wait_for(lambda: (ROOT / "invite.json").exists(), "host invitation")
    invite = json.loads((ROOT / "invite.json").read_text())
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await app.push_screen(JoinScreen(
            prefill=f"omapoker://127.0.0.1:{invite['port']}/{invite['code']}"
        ))
        await pilot.press("enter")
        await wait_for(lambda: isinstance(app.screen, RemoteTableScreen), "guest table")
        remote = app.screen
        checkpoint("guest seated")
        results = set()
        deadline = time.monotonic() + 25.0
        while isinstance(app.screen, RemoteTableScreen):
            assert time.monotonic() < deadline, "Guest stuck at the table"
            state = remote._state or {}
            if state.get("in_hand") and not state.get("complete"):
                for seat in state.get("seats", []):
                    if seat["index"] != remote.client.state.seat:
                        assert not seat["hole"], "Another player's cards leaked"
            result = remote._result
            if result and result["hand_id"] not in results:
                assert verify_deck(bytes.fromhex(result["seed"]), result["deck_commit"])
                results.add(result["hand_id"])
                (ROOT / f"result-{result['hand_id']}").touch()
                checkpoint(f"hand {result['hand_id']} verified")
            if state.get("legal") is not None and not remote._awaiting:
                await pilot.press("c")
            await asyncio.sleep(0.05)
        assert results == set(range(1, HANDS + 1))
        assert isinstance(app.screen, MainMenuScreen)
        assert "host closed" in remote.client.state.closed_reason
        assert app.player.bankroll.chips == STARTING_CHIPS
        assert len(app.screen_stack) == 2
        (ROOT / "guest-left").touch()
        checkpoint("guest received goodbye")


if __name__ == "__main__":
    try:
        asyncio.run(host() if ROLE == "host" else guest())
    finally:
        faulthandler.cancel_dump_traceback_later()
