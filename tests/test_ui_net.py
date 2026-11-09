"""LAN play, driven through the interface.

Two things are checked here that the wire tests cannot check on their own:

* the host's lobby really accepts a client over a real socket, and
* a remote player's view of the table never contains anybody else's cards.

Where a real peer would be needed purely to *drive* the host, a fake connection
with a fake socket is used instead: it exercises the same code path the network
does, without the timing.
"""

from __future__ import annotations

import asyncio
import json
import socket
import time
from types import SimpleNamespace

from textual.widgets import Button, Static

from omapoker.data import DataStore
from omapoker.data.ladder import level_for
from omapoker.engine import cards as C
from omapoker.engine import verify_deck
from omapoker.engine.game import Street
from omapoker.net.client import ClientState, TableClient
from omapoker.net.protocol import LineReader
from omapoker.net.server import (
    ClientConn,
    Event,
    TableServer,
    challenge_message,
    check_auth,
    check_hello,
    make_challenge,
)
from omapoker.session import GameSession, Mode
from omapoker.ui import theme
from omapoker.ui.app import OmaPokerApp
from omapoker.ui.screens.net import (
    HostScreen,
    HostTableScreen,
    JoinScreen,
    RemoteTableScreen,
    bind_remote_seats,
    legal_from_payload,
    legal_payload,
    result_message,
    state_message,
)

MICRO = level_for(5_000)
SIZE = (150, 46)


# --------------------------------------------------------------------------
# scaffolding
# --------------------------------------------------------------------------

def make_store(tmp_path) -> DataStore:
    store = DataStore(tmp_path / "config")
    # No visualiser (never spawn cava) and port 0 (never take a real port).
    store.save_settings({"visualiser": False, "cards_style": "ascii", "lan_port": 0})
    return store


def make_app(tmp_path) -> OmaPokerApp:
    store = make_store(tmp_path)
    store.create_profile("Joseph", "casual")
    return OmaPokerApp(store=store, seed=99)


def make_offline_app(tmp_path) -> OmaPokerApp:
    """An app with a profile loaded, but never mounted."""
    store = make_store(tmp_path)
    profile = store.create_profile("Joseph", "casual")
    app = OmaPokerApp(store=store, seed=99)
    app.settings = store.settings()
    app.player = store.player(profile.id)
    return app


def host_session_for(app, names=("Joseph", "Natalie"), bots: int = 1) -> GameSession:
    """The session a hosted table builds: guest stacks for every remote seat."""
    players = app.guest_players(list(names), MICRO.buy_in)
    return GameSession(
        app.player,
        MICRO,
        store=app.store,
        bot_level="medium",
        bot_count=bots,
        human_names=list(names),
        human_players=players,
        seed=7,
        mode=Mode.HOST,
    )


class FakeSock:
    """A socket that keeps whatever it is sent."""

    def __init__(self) -> None:
        self.sent = bytearray()
        self.closed = False
        self.timeout = None

    def sendall(self, payload: bytes) -> None:
        self.sent.extend(payload)

    def recv(self, size: int) -> bytes:
        # Nothing was ever sent to us, so there is nothing to drain.
        raise BlockingIOError("no data")

    def settimeout(self, value) -> None:
        self.timeout = value

    def shutdown(self, how) -> None:
        self.closed = True

    def close(self) -> None:
        self.closed = True


def fake_remote(server, name: str) -> tuple[ClientConn, FakeSock]:
    """A connection that looks identified to the host, with a fake socket."""
    sock = FakeSock()
    conn = ClientConn(sock, ("127.0.0.1", 5000), server)  # type: ignore[arg-type]
    conn.name = name
    conn.verified = True
    conn.profile_id = "OMA-ID-TEST-0000"
    server._connections.append(conn)
    return conn, sock


def messages(sock: FakeSock) -> list[dict]:
    """Everything a fake connection has been told, in order."""
    return LineReader().feed(bytes(sock.sent))


def typed(sock: FakeSock, kind: str) -> list[dict]:
    return [message for message in messages(sock) if message.get("t") == kind]


def quiet_humans_except(session: GameSession, seat: int, limit: int = 40) -> None:
    """Let everyone but ``seat`` act until it is that seat's turn."""
    for _ in range(limit):
        if session.hand is None or session.hand.street is Street.COMPLETE:
            return
        actor = session.actor()
        if actor is None or actor.index == seat:
            return
        if not actor.is_human:
            session.run_bots()
            continue
        legal = session.legal_actions()
        if legal.can_check:
            session.human_action("check")
        elif legal.can_call:
            session.human_action("call", legal.call_amount)
        else:
            return


def run(coro):
    return asyncio.run(coro)


async def wait_until(predicate, pilot, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
        await pilot.pause()
    return bool(predicate())


def drain(client: TableClient) -> list[dict]:
    """Take everything the host has said and fold it into the client state."""
    taken = client.poll()
    for message in taken:
        client.absorb(message)
    return taken


async def settle(pilot, times: int = 3) -> None:
    for _ in range(times):
        await pilot.pause()


# --------------------------------------------------------------------------
# the wire format of a table
# --------------------------------------------------------------------------

def test_legal_actions_survive_the_wire():
    legal = legal_from_payload(
        {
            "can_fold": True,
            "can_check": False,
            "can_call": True,
            "call_amount": 40,
            "can_bet": False,
            "min_bet": 0,
            "can_raise": True,
            "min_raise_to": 120,
            "max_raise_to": 990,
        }
    )
    assert legal.can_call and legal.call_amount == 40
    assert legal.can_raise and legal.min_raise_to == 120
    assert legal.can_all_in
    assert legal_from_payload(legal_payload(legal)).call_amount == 40
    # Nothing legal at all: the buttons must be dead, not accidentally live.
    dead = legal_from_payload(None)
    assert not dead.can_fold and not dead.can_call and not dead.can_raise
    assert not dead.can_all_in


def test_remote_players_are_seated_in_the_order_they_arrived(tmp_path):
    app = make_offline_app(tmp_path)
    session = host_session_for(app, names=("Joseph", "Natalie", "Samir"))
    server = TableServer("OMA-7K3QF-92", "Joseph's table", host="127.0.0.1", port=0)
    server.start()
    try:
        first, _ = fake_remote(server, "Natalie")
        second, _ = fake_remote(server, "Samir")
        mapping = bind_remote_seats(session, server)

        assert mapping == {first.id: 1, second.id: 2}
        assert session.seats[1].name == "Natalie"
        assert session.seats[2].name == "Samir"
        assert session.seats[1].is_human and session.seats[2].is_human
        assert session.seats[1].profile_id == f"remote-{first.id}"
        assert session.seat_for_profile(f"remote-{second.id}").index == 2
        # The host keeps seat 0 and its own profile.
        assert session.seats[0].profile_id == session.player.profile.id
    finally:
        server.stop()


def test_a_remote_view_only_ever_contains_that_players_cards(tmp_path):
    app = make_offline_app(tmp_path)
    session = host_session_for(app)
    server = TableServer("OMA-7K3QF-92", host="127.0.0.1", port=0)
    server.start()
    try:
        conn, _ = fake_remote(server, "Natalie")
        bind_remote_seats(session, server)
        session.start_hand()
        quiet_humans_except(session, 1)
        assert session.actor().index == 1, "expected seat 1 to be facing a decision"

        message = state_message(session, conn)
        assert message["t"] == "state"
        assert message["you"] == 1
        assert message["legal"] is not None
        assert len(message["seats"][1]["hole"]) == 2
        assert all(
            seat["hole"] == [] for seat in message["seats"] if seat["index"] != 1
        )
        assert json.loads(json.dumps(message)) == message

        # The host's own view is the mirror image of that: its cards, nobody
        # else's, and no legal actions until it is its own turn.
        host = SimpleNamespace(seat=0, id=0, name="host")
        host_message = state_message(session, host)
        assert len(host_message["seats"][0]["hole"]) == 2
        assert host_message["seats"][1]["hole"] == []
        assert host_message["legal"] is None  # seat 1 is the one to act
    finally:
        server.stop()


def test_a_remote_player_who_is_not_to_act_gets_no_legal_actions(tmp_path):
    app = make_offline_app(tmp_path)
    session = host_session_for(app)
    server = TableServer("OMA-7K3QF-92", host="127.0.0.1", port=0)
    server.start()
    try:
        conn, _ = fake_remote(server, "Natalie")
        bind_remote_seats(session, server)
        session.start_hand()
        quiet_humans_except(session, 1)
        idle = state_message(session, SimpleNamespace(seat=0, id=0, name="host"))
        assert idle["legal"] is None
        # And a viewer whose seat is not even human gets nothing at all.
        stranger = state_message(session, SimpleNamespace(seat=None, id=0, name="x"))
        assert stranger["legal"] is None
        assert all(seat["hole"] == [] for seat in stranger["seats"])
    finally:
        server.stop()


def test_the_revealed_seed_matches_the_commitment(tmp_path):
    app = make_offline_app(tmp_path)
    session = host_session_for(app)
    session.start_hand()
    commit = session.hand.deck_commit
    guard = 0
    while session.hand.street is not Street.COMPLETE and guard < 200:
        guard += 1
        actor = session.actor()
        if actor is None:
            break
        if not actor.is_human:
            session.run_bots()
            continue
        legal = session.legal_actions()
        if legal.can_check:
            session.human_action("check")
        elif legal.can_call:
            session.human_action("call", legal.call_amount)
        else:
            break
    assert session.hand.street is Street.COMPLETE

    outcome = session.finish_hand()
    message = result_message(session, outcome)
    assert message["deck_commit"] == commit
    assert verify_deck(bytes.fromhex(message["seed"]), message["deck_commit"])
    assert message["board"] == [C.card_str(card) for card in outcome.board]
    assert set(message["net"]) == {str(seat.index) for seat in session.seats}


# --------------------------------------------------------------------------
# the host's lobby, over a real socket
# --------------------------------------------------------------------------

def test_the_host_lobby_accepts_a_real_client(tmp_path):
    app = make_app(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await app.push_screen(HostScreen())
            await settle(pilot)
            screen = app.screen
            assert isinstance(screen, HostScreen)
            server = app.host_server
            assert server is not None and server.running
            assert server.port > 0

            client = TableClient(app.player, timeout=3.0)
            try:
                client.connect("127.0.0.1", server.port, server.room_code)

                def handshake():
                    drain(client)
                    return client.state.joined

                assert await wait_until(handshake, pilot), (
                    client.state.error or client.state.closed_reason
                )
                assert client.state.host_name == server.host_name
                assert client.state.room_code == server.room_code

                def listed():
                    drain(client)
                    return any(p["name"] == "Joseph" for p in client.state.players)

                assert await wait_until(listed, pilot)
                roster = "\n".join(
                    str(widget.content) for widget in screen.query("Static")
                )
                assert "Joseph" in roster
                assert server.room_code in roster
                # The invitation uses whichever address this machine has, so
                # check the shape rather than a hard-coded address.
                assert "omapoker://" in roster
                assert f":{server.port}/{server.room_code}" in roster
                assert "verified" in roster
                player = server.players()[0]
                assert player.name == "Joseph"
                assert player.verified          # the key signed the nonce
                assert player.seat is None      # nobody is seated until the deal
            finally:
                client.close()
            await settle(pilot)
        assert app.host_server is None  # the lobby closes its socket with the screen

    run(scenario())


def test_a_lobby_connection_with_the_wrong_room_code_is_refused(tmp_path):
    app = make_app(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await app.push_screen(HostScreen())
            await settle(pilot)
            server = app.host_server
            client = TableClient(app.player, timeout=3.0)
            try:
                client.connect("127.0.0.1", server.port, "OMA-AAAAA-11")

                def refused():
                    drain(client)
                    return bool(client.state.error) or not client.connected

                assert await wait_until(refused, pilot)
                assert client.state.error, "a refusal needs a reason"
                assert not client.state.joined
                assert server.players() == []
            finally:
                client.close()

    run(scenario())


def test_a_client_that_never_says_hello_is_dropped(tmp_path):
    app = make_app(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await app.push_screen(HostScreen())
            await settle(pilot)
            server = app.host_server
            sock = socket.create_connection(("127.0.0.1", server.port), timeout=3)
            try:
                assert await wait_until(lambda: server.open_connections() > 0, pilot)
                assert len(server.kick_idle(age=0.0)) == 1
                assert server.open_connections() == 0
            finally:
                sock.close()

    run(scenario())


# --------------------------------------------------------------------------
# a joining player's screen
# --------------------------------------------------------------------------

class FakeClient(TableClient):
    """The client half of the table, without a socket."""

    def __init__(self, player, *, seat: int = 1) -> None:
        super().__init__(player)
        self.state = ClientState(
            connected=True,
            joined=True,
            seat=seat,
            host_name="Joseph's table",
            room_code="OMA-7K3QF-92",
        )
        self.sent: list[dict] = []
        self.inbox: list[dict] = []

    def poll(self, limit: int = 64) -> list[dict]:
        out = list(self.inbox)
        self.inbox.clear()
        return out

    def send(self, message: dict) -> bool:
        self.sent.append(message)
        return True

    def action(self, action: str, amount: int = 0) -> bool:
        return self.send({"t": "action", "action": action, "amount": amount})

    def ready(self) -> bool:
        return self.send({"t": "ready"})

    def close(self, reason: str = "") -> None:
        self.state.connected = False
        self.state.closed_reason = reason

    def feed(self, message: dict) -> None:
        self.inbox.append(message)


def test_a_joined_guest_can_wait_in_the_lobby_past_the_handshake_deadline(tmp_path):
    app = make_app(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            joining = JoinScreen()
            await app.push_screen(joining)
            client = FakeClient(app.player)
            client.state.joined = False
            client.state.seat = None
            joining.client = client
            joining._deadline = time.monotonic() - 1.0
            client.feed({"t": "welcome", "seat": None, "host": "Joseph's table"})
            joining.pump()

            assert joining.client is client
            assert client.connected
            assert "Waiting for" in str(joining.query_one("#join-status", Static).content)
            await pilot.press("escape")
            await settle(pilot)
            assert not client.connected
            assert joining.client is None

    run(scenario())


def test_the_remote_screen_renders_the_hosts_view(tmp_path):
    app = make_app(tmp_path)
    server = TableServer("OMA-7K3QF-92", host="127.0.0.1", port=0)
    server.start()

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            session = host_session_for(app)
            conn, _ = fake_remote(server, "Natalie")
            bind_remote_seats(session, server)
            session.start_hand()
            quiet_humans_except(session, 1)

            client = FakeClient(app.player, seat=1)
            await app.push_screen(RemoteTableScreen(client))
            await settle(pilot)
            screen = app.screen
            assert isinstance(screen, RemoteTableScreen)

            client.feed(state_message(session, conn))
            screen.pump()
            await settle(pilot)

            text = "\n".join(
                str(widget.content) for widget in screen.query("Static")
            )
            hole = session.seats[1].hole
            style = theme.cards_style(app.player.profile.cards_style)
            assert theme.card_markup(hole[0], style) in text
            assert theme.card_markup(hole[1], style) in text
            assert f"{session.hand.total_pot:,}" in text
            assert "Natalie" in text  # the seat is labelled from the host's view

            # Their turn: the bar is live and an action goes down the wire.
            assert not screen.query_one("#btn-call", Button).disabled
            assert not screen.query_one("#btn-fold", Button).disabled
            screen.action_fold()
            assert client.sent[-1] == {"t": "action", "action": "fold", "amount": 0}
            screen.action_ready()
            assert client.sent[-1] == {"t": "ready"}

    run(scenario())
    server.stop()


def test_the_remote_screen_refuses_actions_that_are_not_offered(tmp_path):
    app = make_app(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            client = FakeClient(app.player, seat=1)
            await app.push_screen(RemoteTableScreen(client))
            await settle(pilot)
            screen = app.screen

            client.feed(
                {
                    "t": "state",
                    "hand_id": 1,
                    "seats": [
                        {"index": 0, "name": "Joseph", "stack": 5_000, "hole": []},
                        {"index": 1, "name": "Natalie", "stack": 5_000, "hole": []},
                    ],
                    "board": [],
                    "pot": 30,
                    "to_act": 0,
                    "button": 0,
                    "legal": None,
                }
            )
            screen.pump()
            await settle(pilot)

            assert screen.query_one("#btn-fold", Button).disabled
            assert screen.query_one("#btn-call", Button).disabled
            screen.action_fold()          # not offered, so it never leaves
            screen.action_raise_mode()
            assert client.sent == []
            assert "Waiting for Joseph" in str(screen.query_one("#coach-panel").content)

    run(scenario())


def test_a_guest_can_leave_with_the_documented_q_key(tmp_path):
    from omapoker.ui.screens.base import ConfirmScreen
    from omapoker.ui.screens.menu import MainMenuScreen

    app = make_app(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            client = FakeClient(app.player)
            await app.push_screen(RemoteTableScreen(client))
            await pilot.press("q")
            assert isinstance(app.screen, ConfirmScreen)
            await pilot.press("enter")
            await settle(pilot)
            assert isinstance(app.screen, MainMenuScreen)
            assert not client.connected
            assert len(app.screen_stack) == 2

    run(scenario())


def test_the_remote_screen_goes_home_when_the_host_says_goodbye(tmp_path):
    app = make_app(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            client = FakeClient(app.player, seat=1)
            await app.push_screen(RemoteTableScreen(client))
            await settle(pilot)
            client.feed({"t": "bye", "reason": "the host closed the table"})
            app.screen.pump()
            await settle(pilot)
            assert app.session is None
            assert app.host_server is None
            assert app.screen is not None

    run(scenario())


# --------------------------------------------------------------------------
# the hosted table
# --------------------------------------------------------------------------

async def open_hosted_table(app, server, pilot, remote="Natalie"):
    """Start a hosted table with one fake remote player already in the lobby."""
    conn, sock = fake_remote(server, remote)
    app.host_server = server
    app.start_session(
        MICRO,
        mode=Mode.HOST,
        human_names=["Joseph", remote],
        bot_count=1,
    )
    await settle(pilot)
    screen = app.screen
    assert isinstance(screen, HostTableScreen), f"got {type(screen).__name__}"
    await wait_until(lambda: app.session.hand is not None, pilot)
    return screen, conn, sock


def test_the_hosted_table_seats_the_remote_and_broadcasts(tmp_path):
    app = make_app(tmp_path)
    server = TableServer("OMA-7K3QF-92", "Joseph's table", host="127.0.0.1", port=0)
    server.start()

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            screen, conn, sock = await open_hosted_table(app, server, pilot)
            session = app.session

            assert conn.seat == 1
            assert session.seats[1].name == "Natalie"
            assert session.seats[1].is_human
            assert screen._my_seats == {0}

            welcome = typed(sock, "welcome")
            assert welcome and welcome[-1]["seat"] == 1
            assert welcome[-1]["code"] == server.room_code
            assert welcome[-1]["level"]["buy_in"] == MICRO.buy_in

            deals = typed(sock, "deal")
            assert deals and deals[-1]["deck_commit"]
            states = typed(sock, "state")
            assert states, "the remote player was never sent the table"
            assert states[-1]["you"] == 1
            assert len(states[-1]["seats"]) == len(session.seats)

            # The host sees its own cards and not the guest's.
            style = theme.cards_style(app.player.profile.cards_style)
            host_card = str(screen.query_one("#seat-0").content)
            assert theme.card_markup(session.seats[0].hole[0], style) in host_card
            guest_card = str(screen.query_one("#seat-1").content)
            assert all(
                theme.card_markup(card, style) not in guest_card
                for card in session.seats[1].hole
            )

    run(scenario())
    server.stop()


def test_a_hand_is_played_across_the_wire(tmp_path):
    app = make_app(tmp_path)
    server = TableServer("OMA-7K3QF-92", host="127.0.0.1", port=0)
    server.start()

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            screen, conn, sock = await open_hosted_table(app, server, pilot)
            session = app.session
            assert typed(sock, "deal"), "no hand was dealt"

            for _ in range(200):
                if screen._hand_over:
                    break
                actor = session.actor()
                if actor is None:
                    await pilot.pause()
                    continue
                legal = session.legal_actions()
                if actor.index == 0:
                    if legal.can_check:
                        screen.action_check_call()
                    else:
                        screen.action_fold()
                else:
                    action = "check" if legal.can_check else "call"
                    amount = 0 if legal.can_check else legal.call_amount
                    screen.remote_message(
                        conn, {"t": "action", "action": action, "amount": amount}
                    )
                await pilot.pause()
            assert screen._hand_over, "the hand never finished"

            # Nothing ever leaked: no other seat's cards before the showdown.
            for message in typed(sock, "state"):
                for entry in message["seats"]:
                    if entry["index"] != 1 and entry["hole"]:
                        assert message["complete"], "a card leaked mid-hand"

            results = typed(sock, "result")
            assert results, "the finished hand was never announced"
            result = results[-1]
            assert verify_deck(bytes.fromhex(result["seed"]), result["deck_commit"])
            assert result["net"]

    run(scenario())
    server.stop()


def test_a_remote_player_who_vanishes_is_replaced_by_a_bot(tmp_path):
    app = make_app(tmp_path)
    server = TableServer("OMA-7K3QF-92", host="127.0.0.1", port=0)
    server.start()

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            screen, conn, sock = await open_hosted_table(app, server, pilot)
            session = app.session
            assert session.seats[1].is_human

            screen.remote_left(conn)
            assert not session.seats[1].is_human
            assert session.seats[1].is_bot
            assert conn.seat is None

            for _ in range(200):
                if screen._hand_over:
                    break
                actor = session.actor()
                if actor is not None and actor.index == 0:
                    screen.action_check_call()
                elif actor is not None and actor.is_bot:
                    session.run_bots()
                await pilot.pause()
            assert screen._hand_over

    run(scenario())
    server.stop()


def test_a_silent_remote_player_is_acted_for_on_the_clock(tmp_path):
    app = make_app(tmp_path)
    server = TableServer("OMA-7K3QF-92", host="127.0.0.1", port=0)
    server.start()

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            screen, conn, sock = await open_hosted_table(app, server, pilot)
            session = app.session
            logged: list[str] = []
            screen._log = lambda text, kind="info": logged.append(text)

            for _ in range(60):
                actor = session.actor()
                if actor is not None and actor.index == 1:
                    break
                if actor is not None and actor.index == 0:
                    screen.action_check_call()
                elif actor is not None and actor.is_bot:
                    session.run_bots()
                await pilot.pause()
            assert session.actor().index == 1

            screen.tick_clock()  # notes the turn and when it started
            screen._turn_started -= 100.0  # they went to make coffee
            screen.tick_clock()
            assert any("ran out of time" in line for line in logged)
            assert any(
                "check" in event.text or "fold" in event.text
                for event in session.hand.events
            )
            assert screen._turn_key is None  # the clock is wound for the next turn

    run(scenario())
    server.stop()


def test_a_guest_who_busts_is_bought_back_in_by_the_house(tmp_path):
    app = make_app(tmp_path)
    server = TableServer("OMA-7K3QF-92", host="127.0.0.1", port=0)
    server.start()

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            screen, conn, sock = await open_hosted_table(app, server, pilot)
            session = app.session
            logged: list[str] = []
            screen._log = lambda text, kind="info": logged.append(text)

            session.seats[1].stack = 0
            session.seats[1].sitting_out = False
            screen._handle_busts()
            await pilot.pause()

            assert app.screen is screen  # no modal about somebody else's chips
            assert session.seats[1].stack == MICRO.buy_in
            assert not session.seats[1].sitting_out
            assert any("buys in again" in line for line in logged)

    run(scenario())
    server.stop()


def test_the_host_busting_gets_its_own_prompt(tmp_path):
    app = make_app(tmp_path)
    server = TableServer("OMA-7K3QF-92", host="127.0.0.1", port=0)
    server.start()

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            from omapoker.ui.screens.base import ChoiceScreen

            screen, conn, sock = await open_hosted_table(app, server, pilot)
            app.session.seats[0].stack = 0
            app.session.seats[0].sitting_out = False
            screen._handle_busts()
            await settle(pilot)
            assert isinstance(app.screen, ChoiceScreen)
            app.pop_screen()
            await settle(pilot)

    run(scenario())
    server.stop()


def test_a_latecomer_is_turned_away_after_the_deal(tmp_path):
    app = make_app(tmp_path)
    server = TableServer("OMA-7K3QF-92", host="127.0.0.1", port=0)
    server.start()

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            screen, conn, sock = await open_hosted_table(app, server, pilot)
            newcomer, late_sock = fake_remote(server, "Latecomer")
            screen.remote_message(
                newcomer, {"t": "hello", "version": 1, "name": "Latecomer"}
            )
            rejected = typed(late_sock, "reject")
            assert rejected and "already started" in rejected[-1]["reason"]
            assert newcomer not in server.connections
            assert newcomer not in server.players()

    run(scenario())
    server.stop()


def test_the_lobby_hands_the_server_over_to_the_table(tmp_path):
    """The lobby stays mounted under the table: it must give the events up.

    Both screens drain the same queue, so if the lobby kept polling it would
    take turns with the table answering the players: half of every action would
    be handled by a screen the host has already left behind, and the table would
    sit waiting for an action that was never going to arrive.
    """
    app = make_app(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await app.push_screen(HostScreen())
            await settle(pilot)
            lobby = app.screen
            assert isinstance(lobby, HostScreen)
            server = app.host_server
            assert server is not None

            other = app.store.create_profile("Natalie", "casual")
            client = TableClient(app.store.player(other.id), timeout=3.0)
            try:
                client.connect("127.0.0.1", server.port, server.room_code)
                assert await wait_until(
                    lambda: (drain(client), client.state.joined)[1], pilot
                )
                guest = server.players()[0]
                assert guest.name == "Natalie"

                # The real path: the session starts, then the table goes on top.
                app.start_session(
                    MICRO,
                    mode=Mode.HOST,
                    human_names=["Joseph", "Natalie"],
                    bot_count=1,
                )
                await settle(pilot)
                table = app.screen
                assert isinstance(table, HostTableScreen)
                assert lobby in app.screen_stack, "the lobby is still underneath"
                session = app.session
                assert await wait_until(lambda: session.hand is not None, pilot)

                # Anything that arrives now is the table's to answer.
                answered: list[dict] = []
                logged: list[str] = []
                lobby.handle_message = lambda conn, message: answered.append(message)
                table._log = lambda text, kind="info": logged.append(text)
                server._events.put(Event("message", guest, {"t": "ready", "at": 0.0}))

                lobby.pump()  # exactly what the lobby's own timer would do
                assert answered == [], "the lobby answered a message meant for the table"
                assert logged == [], "the message was consumed twice"

                table.pump()  # and the table's own timer
                assert answered == []
                assert any("ready" in line for line in logged)

                # And the real thing: a keypress on the guest's machine lands.
                quiet_humans_except(session, guest.seat)
                assert session.actor().index == guest.seat
                client.action("fold")
                assert await wait_until(
                    lambda: session.seats[guest.seat].folded, pilot
                ), "the guest's fold never reached the table"
                assert answered == []
            finally:
                client.close()

    run(scenario())


def test_the_host_uses_the_current_roster_when_the_buy_in_is_chosen(tmp_path):
    from textual.widgets import ListView
    from omapoker.ui.screens.ladder import TablePickerScreen

    app = make_app(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await app.push_screen(HostScreen())
            server = app.host_server
            first, _ = fake_remote(server, "Natalie")
            await pilot.press("s")
            assert isinstance(app.screen, TablePickerScreen)
            # One guest leaves and another arrives while the picker is up.
            server.remove(first)
            current, _ = fake_remote(server, "Jeremiah")
            app.screen.query_one("#ladder-list", ListView).index = 95
            await pilot.press("enter")
            await settle(pilot)

            assert isinstance(app.screen, HostTableScreen)
            assert [seat.name for seat in app.session.seats if seat.is_human] == ["Joseph", "Jeremiah"]
            assert current.seat == 1
            assert app.session.seats[current.seat].is_human

    run(scenario())


def test_leaving_the_hosted_table_says_goodbye_to_everyone(tmp_path):
    app = make_app(tmp_path)
    server = TableServer("OMA-7K3QF-92", host="127.0.0.1", port=0)
    server.start()

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            screen, conn, sock = await open_hosted_table(app, server, pilot)
            screen._close_everything()
            await settle(pilot)
            assert app.session is None
            assert app.host_server is None
            assert not server.running
            goodbye = typed(sock, "bye")
            assert goodbye and "host closed" in goodbye[-1]["reason"]
            assert app.screen is not None

    run(scenario())


# --------------------------------------------------------------------------
# the whole joining trip, over a real socket
# --------------------------------------------------------------------------

def test_a_guest_joins_over_a_socket_and_plays_a_hand(tmp_path):
    """Paste an invite, land at the table, and have a keypress reach the host.

    Textual cannot run two applications in one process, so the host half is
    scripted here: the *protocol* is answered with the same helpers the host
    screen uses, while the guest half is the real screen on a real socket.
    """
    app = make_app(tmp_path)
    server = TableServer("OMA-7K3QF-92", "Joseph's table", host="127.0.0.1", port=0)
    server.start()

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            session = host_session_for(app, names=("Joseph", "Natalie"))
            invite = f"omapoker://127.0.0.1:{server.port}/{server.room_code}"
            await app.push_screen(JoinScreen(prefill=invite))
            await settle(pilot)
            joining = app.screen
            assert isinstance(joining, JoinScreen)

            joining.attempt()
            assert joining.client is not None
            assert "Connecting" in str(joining.query_one("#join-status", Static).content)

            nonce: bytes | None = None
            conn = None
            dealt = False
            for _ in range(500):
                for event in server.poll():
                    if event.kind != "message" or event.conn is None:
                        continue
                    conn = event.conn
                    message = event.message or {}
                    kind = message.get("t")

                    if kind == "hello":
                        assert check_hello(message, server.room_code).ok
                        conn.name = str(message["name"])
                        conn.public_hex = str(message["public"])
                        conn.profile_id = str(message["code"])
                        nonce = make_challenge()
                        conn.nonce = nonce
                        conn.send(challenge_message(nonce))
                        # The lobby: seated, but no chair yet.
                        conn.send(
                            {
                                "t": "welcome",
                                "seat": None,
                                "code": server.room_code,
                                "host": server.host_name,
                            }
                        )
                        conn.send(
                            {
                                "t": "lobby",
                                "players": [
                                    {"name": "Joseph", "verified": True, "ready": False}
                                ],
                            }
                        )

                    elif kind == "auth" and nonce is not None:
                        verdict = check_auth(conn, message, nonce)
                        assert verdict.ok, verdict.reason
                        assert verdict.verified  # a real Ed25519 signature
                        conn.verified = True
                        # The host deals: bind the guest to a chair, then start.
                        bind_remote_seats(session, server)
                        session.start_hand()
                        conn.send(
                            {
                                "t": "welcome",
                                "seat": conn.seat,
                                "code": server.room_code,
                                "host": server.host_name,
                                "level": {
                                    "name": session.level.name,
                                    "buy_in": session.level.buy_in,
                                    "small_blind": session.level.small_blind,
                                    "big_blind": session.level.big_blind,
                                },
                            }
                        )
                        conn.send(state_message(session, conn))
                        dealt = True

                joining.pump()
                if dealt and isinstance(app.screen, RemoteTableScreen):
                    break
                await asyncio.sleep(0.02)

            assert dealt, "the handshake never completed"
            remote = app.screen
            assert isinstance(remote, RemoteTableScreen)
            await settle(pilot)  # the ring mounts on the next refresh
            assert remote.client.state.seat == conn.seat == 1
            assert remote.client.state.host_name == server.host_name
            assert remote.client.state.room_code == server.room_code

            # The guest sees its own cards on screen.
            style = theme.cards_style(app.player.profile.cards_style)
            view = "\n".join(str(widget.content) for widget in remote.query("Static"))
            assert theme.card_markup(session.seats[1].hole[0], style) in view

            # Let the host's seat act until the guest is the one to decide.
            for _ in range(40):
                actor = session.actor()
                if actor is None or actor.index == 1:
                    break
                if actor.is_bot:
                    session.run_bots()
                    continue
                legal = session.legal_actions()
                session.human_action(
                    "check" if legal.can_check else "call",
                    0 if legal.can_check else legal.call_amount,
                )
            assert session.actor() is not None and session.actor().index == 1

            # The host broadcasts the turn; the guest presses F; the host sees it.
            conn.send(state_message(session, conn))
            remote.pump()
            await settle(pilot)
            remote.action_fold()

            folded = False
            for _ in range(200):
                for event in server.poll():
                    if event.kind != "message" or event.conn is None:
                        continue
                    message = event.message or {}
                    if message.get("t") == "action":
                        session.apply_remote(
                            int(event.conn.seat), str(message["action"]), int(message.get("amount") or 0)
                        )
                        folded = True
                if folded:
                    break
                await asyncio.sleep(0.02)

            assert folded, "the guest's fold never reached the host"
            assert session.seats[1].folded
            assert session.seats[1].last_action == "fold"

    run(scenario())
    server.stop()
