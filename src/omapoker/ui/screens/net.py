"""LAN screens: the host's lobby, the hosted table, and the joining player's view.

The host owns the deck. Everything a remote player sees is a broadcast of the
host's own view of the table, with their hole cards filled in and nobody else's.
The host's interface applies every action, so the engine still lives on exactly
one thread and a hacked client can only ask for illegal actions, which the
engine refuses.

When a player vanishes mid-hand their seat is handed to a bot, because a table
that freezes waiting for a laptop that went to sleep is worse than a table with
one more bot at it.
"""

from __future__ import annotations

import time

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, Vertical, VerticalScroll
from textual.timer import Timer
from textual.widgets import Button, Footer, Input, Static

from ...engine import cards as C
from ...engine.evaluator import evaluate, hand_name_short
from ...engine.game import IllegalAction, LegalActions, Street
from ...music import idle_wave
from ...net.client import TableClient
from ...net.protocol import ProtocolError, join_string, local_addresses, new_room_code, parse_join_string
from ...net.server import (
    TableServer,
    challenge_message,
    check_auth,
    check_hello,
    make_challenge,
)
from ...session import Mode
from .. import theme
from ..widgets import (
    ActionBar,
    BoardPanel,
    CoachPanel,
    Room,
    LogPanel,
    MusicChip,
    PokerTable,
    SeatCard,
)
from .base import BaseScreen, ConfirmScreen
from .table import BOARD, TableScreen, ring_rows

POLL_INTERVAL = 0.25
MUSIC_INTERVAL = 1.5
BAR_INTERVAL = 0.15


# --------------------------------------------------------------------------
# wire helpers
# --------------------------------------------------------------------------

def legal_payload(legal: LegalActions | None) -> dict | None:
    if legal is None:
        return None
    return {
        "can_fold": bool(legal.can_fold),
        "can_check": bool(legal.can_check),
        "can_call": bool(legal.can_call),
        "call_amount": int(legal.call_amount),
        "can_bet": bool(legal.can_bet),
        "min_bet": int(legal.min_bet),
        "can_raise": bool(legal.can_raise),
        "min_raise_to": int(legal.min_raise_to),
        "max_raise_to": int(legal.max_raise_to),
    }


def legal_from_payload(payload: dict | None) -> LegalActions:
    if not payload:
        return LegalActions(can_fold=False)
    return LegalActions(
        can_fold=bool(payload.get("can_fold")),
        can_check=bool(payload.get("can_check")),
        can_call=bool(payload.get("can_call")),
        call_amount=int(payload.get("call_amount") or 0),
        can_bet=bool(payload.get("can_bet")),
        min_bet=int(payload.get("min_bet") or 0),
        can_raise=bool(payload.get("can_raise")),
        min_raise_to=int(payload.get("min_raise_to") or 0),
        max_raise_to=int(payload.get("max_raise_to") or 0),
    )


def bind_remote_seats(session, server: TableServer) -> dict[int, int]:
    """Give every identified player the seat their name was seated at.

    ``_build_seats`` seats humans first, in the order they were named, so the
    host is seat 0 and the *n*-th remote player is seat *n*.
    """
    mapping: dict[int, int] = {}
    for offset, conn in enumerate(server.players()):
        seat_index = offset + 1
        if seat_index >= len(session.seats):
            break
        seat = session.seats[seat_index]
        seat.name = conn.name[:24] or seat.name
        seat.profile_id = f"remote-{conn.id}"
        conn.seat = seat_index
        mapping[conn.id] = seat_index
    return mapping


def state_message(session, conn) -> dict:
    """One player's view of the table, ready for the wire."""
    seat = conn.seat
    viewer = seat if seat is not None and session.seats[seat].is_human else None
    payload = session.state(viewer=viewer)
    payload["t"] = "state"
    payload["you"] = seat
    payload["deck_commit"] = session.hand.deck_commit if session.hand else ""
    payload["events"] = [event.text for event in session.recent_events(10)]
    legal = None
    if seat is not None and session.awaiting_human:
        actor = session.active_human
        if actor is not None and actor.index == seat:
            legal = legal_payload(session.legal_actions())
    payload["legal"] = legal
    return payload


def result_message(session, outcome) -> dict:
    return {
        "t": "result",
        "hand_id": outcome.hand_id,
        "board": [C.card_str(card) for card in outcome.board],
        "reveal": {
            str(index): [C.card_str(card) for card in hole]
            for index, hole in outcome.reveal.items()
        },
        "net": {str(index): net for index, net in outcome.net.items()},
        "seed": session.hand.seed.hex() if session.hand else "",
        "deck_commit": session.hand.deck_commit if session.hand else "",
        "showdown": bool(outcome.showdown),
    }


# --------------------------------------------------------------------------
# hosting
# --------------------------------------------------------------------------

class HostScreen(BaseScreen):
    """The lobby: a room code, an invitation, and whoever has turned up."""

    BINDINGS = [Binding("s", "start", "Start the table", show=True)]
    def __init__(self) -> None:
        super().__init__()
        self.code = new_room_code()
        self.server: TableServer | None = None
        self._notice = ""
        self._poll: Timer | None = None

    # -- layout --------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Static(
            f"[{theme.ACCENT}][b]Hosting a LAN table[/b][/]  "
            f"[{theme.TEXT_DIM}]everyone needs Omarchy and the room code[/]",
            id="panel-title",
        )
        with VerticalScroll(id="panel-scroll"):
            yield Static("", id="host-room")
            yield Static("", id="host-players")
            yield Static(f"[{theme.ACCENT}][b]How this works[/b][/]", classes="section-title")
            yield Static(
                f"[{theme.TEXT_DIM}]Your machine is the dealer: it owns the deck, the "
                f"stacks and the clock. Everyone else sends actions and sees the "
                f"table from their own seat. Each hand's deck is committed to "
                f"before the cards come out and the seed is revealed afterwards, "
                f"so nobody can change the deck once the cards are known.\n"
                f"Your coach stays on your screen. Guests can still see their own "
                f"cards, the board and the pot.[/]"
            )
            yield Static(f"[{theme.ACCENT}][b]Players[/b][/]", classes="section-title")
            yield Static("", id="host-roster")
        yield Horizontal(
            Button("Start the table", id="start", variant="success"),
            Button("Kick the last player", id="kick"),
            Button("Stop hosting", id="stop"),
            Button("Back", id="back"),
            id="host-controls",
        )
        yield Footer()

    def on_mount(self) -> None:
        app = self.app
        host_name = str(
            app.settings.get("host_name") or app.player.profile.name or "OmaPoker table"
        )
        configured = app.settings.get("lan_port")
        port = 8760 if configured is None else int(configured)
        self.server = TableServer(self.code, host_name, port=port)
        try:
            self.server.start()
        except OSError:
            # The configured port is taken; any free port will do, the guests
            # get the whole invitation anyway.
            self.server = TableServer(self.code, host_name, port=0)
            try:
                self.server.start()
            except OSError as exc:
                self.notify(f"Could not open a listening socket: {exc}", severity="error")
                self.server = None
                self.app.pop_screen()
                return
            self.notify(f"Port {port} was busy; using {self.server.port} instead.")
        app.host_server = self.server
        self._poll = self.set_interval(POLL_INTERVAL, self.pump)
        self.refresh_lobby()

    def on_unmount(self) -> None:
        self.stop_polling()
        if self.server is not None and self.app.session is None:
            # Leaving the lobby without starting: stop listening.
            self.close_server()

    def stop_polling(self) -> None:
        """Stop reading the server's events.

        The lobby stays mounted under the table screen once the deal starts, so
        without this both screens would drain the same queue and take turns
        answering the players - which is how a fold gets delivered to a screen
        nobody is looking at, and never reaches the table.
        """
        poll, self._poll = self._poll, None
        if poll is not None:
            poll.stop()

    # -- the lobby -----------------------------------------------------
    def refresh_lobby(self) -> None:
        server = self.server
        if server is None:
            return
        addresses = local_addresses()
        lines = [
            f"[{theme.TEXT}]Room code[/]  [{theme.ACCENT}][b]{server.room_code}[/][/]",
            "",
        ]
        for address in addresses:
            lines.append(
                f"[{theme.TEXT_DIM}]invitation[/] [{theme.TEXT}]"
                f"{join_string(address, server.port, server.room_code)}[/]"
            )
        lines.append(
            f"[{theme.TEXT_DIM}]listening on port {server.port}; "
            f"players type the room code, or paste the whole invitation into "
            f"Join a LAN table[/]"
        )
        self.query_one("#host-room", Static).update("\n".join(lines))

        players = server.players()
        self.query_one("#host-players", Static).update(
            f"[{theme.TEXT}]Connected[/]  [{theme.ACCENT}]{len(players)}[/]"
            f"[{theme.TEXT_DIM}] of {server.max_players} seats, "
            f"{server.open_connections() - len(players)} still saying hello[/]"
        )
        roster = []
        for position, conn in enumerate(players, start=1):
            mark = "·" if conn.verified else "?"
            kind = "verified key" if conn.verified else "unverified label"
            roster.append(
                f"[{theme.ACCENT}]{mark}[/] [{theme.TEXT}]seat {position}[/] "
                f"[{theme.TEXT}]{conn.name}[/]  "
                f"[{theme.TEXT_DIM}]{kind} from {conn.address[0]}[/]"
            )
        if not roster:
            roster.append(
                f"[{theme.TEXT_DIM}]Nobody yet. The table will fill the empty "
                f"seats with bots.[/]"
            )
        if self._notice:
            roster.append(f"[{theme.WARN}]{self._notice}[/]")
        self.query_one("#host-roster", Static).update("\n".join(roster))

    # -- network -------------------------------------------------------
    def pump(self) -> None:
        server = self.server
        if server is None:
            return
        if self.app.session is not None:
            # The table screen is up: it owns the server's events now.
            self.stop_polling()
            return
        for event in server.poll():
            if event.kind == "message" and event.conn is not None:
                self.handle_message(event.conn, event.message or {})
            elif event.kind == "left" and event.conn is not None:
                self.notice(f"{event.conn.label} left the lobby.")
        server.kick_idle()
        self.refresh_lobby()

    def handle_message(self, conn, message: dict) -> None:
        server = self.server
        if server is None:
            return
        kind = message.get("t")
        if kind == "hello":
            verdict = check_hello(message, server.room_code)
            if not verdict.ok:
                server.send(conn, {"t": "reject", "reason": verdict.reason})
                conn.close(abort=True)
                server.remove(conn)
                self.notice(f"Rejected a connection: {verdict.reason}.")
                return
            conn.name = str(message.get("name") or "")[:24]
            conn.public_hex = str(message.get("public") or "")
            conn.profile_id = str(message.get("code") or "")
            conn.verified = verdict.verified
            conn.nonce = make_challenge()
            server.send(conn, challenge_message(conn.nonce))
        elif kind == "auth":
            verdict = check_auth(conn, message, conn.nonce)
            if not verdict.ok:
                server.send(conn, {"t": "reject", "reason": verdict.reason})
                conn.close(abort=True)
                server.remove(conn)
                self.notice(f"Rejected {conn.name or 'a connection'}: {verdict.reason}.")
                return
            conn.verified = True
            server.send(
                conn,
                {
                    "t": "welcome",
                    "seat": None,
                    "host": server.host_name,
                    "code": server.room_code,
                },
            )
            self.broadcast_lobby()
            self.notice(f"{conn.name} sat down in the lobby.")
        elif kind == "ready":
            conn.ready = True
            self.broadcast_lobby()
        elif kind == "bye":
            conn.close()
            server.remove(conn)
            self.broadcast_lobby()
        else:
            # In the lobby there is nothing else to do with a message.
            pass

    def broadcast_lobby(self) -> None:
        server = self.server
        if server is None:
            return
        players = [
            {"name": conn.name, "verified": conn.verified, "ready": conn.ready}
            for conn in server.players()
        ]
        server.broadcast({"t": "lobby", "players": players})

    def notice(self, text: str) -> None:
        self._notice = text

    # -- controls ------------------------------------------------------
    def on_button_pressed(self, event: Button.Pressed) -> None:
        identifier = event.button.id or ""
        if identifier == "start":
            self.action_start()
        elif identifier == "kick":
            self.kick_last()
        elif identifier in ("stop", "back"):
            self.close_server()
            self.action_back()

    def action_start(self) -> None:
        server = self.server
        if server is None:
            return
        from .ladder import TablePickerScreen

        host = self.app.player.profile.name
        names = [host] + [conn.name for conn in server.players()]
        # The server stays on the app (``app.host_server``); the table screen
        # picks it up when the session starts.
        self.app.push_screen(TablePickerScreen(Mode.HOST, names))

    def kick_last(self) -> None:
        server = self.server
        if server is None:
            return
        players = server.players()
        if not players:
            self.notify("Nobody to kick.", severity="warning")
            return
        victim = players[-1]
        server.send(victim, {"t": "reject", "reason": "the host removed you"})
        victim.close(abort=True)
        server.remove(victim)
        self.notice(f"Removed {victim.name}.")
        self.broadcast_lobby()
        self.refresh_lobby()

    def close_server(self) -> None:
        self.stop_polling()
        if self.server is not None:
            self.server.stop()
        if self.app.host_server is self.server:
            self.app.host_server = None
        self.server = None


class HostTableScreen(TableScreen):
    """The table, with a network attached.

    The only differences from the offline table: this keyboard controls exactly
    one seat, the table broadcasts itself after every change, and remote actions
    arrive on a timer instead of a keypress.
    """

    def __init__(self, server: TableServer) -> None:
        super().__init__()
        self.server = server
        self._my_seats = {0}
        self._announced_hand = 0
        self._ready: set[int] = set()
        self._turn_key: tuple | None = None
        self._turn_started = time.monotonic()

    # -- lifecycle -----------------------------------------------------
    def on_mount(self) -> None:
        super().on_mount()
        session = self.app.session
        if session is not None:
            bound = bind_remote_seats(session, self.server)
            self._log(
                f"Table hosted as {self.server.room_code}; "
                f"{len(bound)} remote player(s) seated.",
                "info",
            )
        self.set_interval(POLL_INTERVAL, self.pump)
        self.set_interval(1.0, self.tick_clock)
        self.refresh_lobby_seats()

    def on_unmount(self) -> None:
        super().on_unmount()

    def refresh_lobby_seats(self) -> None:
        """Tell every player which seat they own."""
        session = self.app.session
        if session is None:
            return
        for conn in self.server.players():
            if conn.seat is None:
                continue
            self.server.send(
                conn,
                {
                    "t": "welcome",
                    "seat": conn.seat,
                    "host": self.server.host_name,
                    "code": self.server.room_code,
                    "level": {
                        "name": session.level.name,
                        "buy_in": session.level.buy_in,
                        "small_blind": session.level.small_blind,
                        "big_blind": session.level.big_blind,
                    },
                },
            )

    # -- pump ----------------------------------------------------------
    def pump(self) -> None:
        for event in self.server.poll():
            if event.kind == "message" and event.conn is not None:
                self.remote_message(event.conn, event.message or {})
            elif event.kind == "left" and event.conn is not None:
                self.remote_left(event.conn)
        self.server.kick_idle()

    def remote_message(self, conn, message: dict) -> None:
        session = self.app.session
        kind = message.get("t")
        if kind == "hello":
            # The table is already running: a newcomer cannot be seated now.
            self.server.send(
                conn, {"t": "reject", "reason": "this table has already started"}
            )
            conn.close(abort=True)
            self.server.remove(conn)
            self._log(f"Turned away {conn.address[0]}: the table is full.", "info")
            return
        if kind == "action":
            if session is None or conn.seat is None:
                return
            actor = session.actor()
            if actor is None or actor.index != conn.seat:
                self.server.send(conn, {"t": "reject", "reason": "it is not your turn"})
                return
            try:
                session.apply_remote(
                    conn.seat,
                    str(message.get("action") or ""),
                    int(message.get("amount") or 0),
                )
            except (IllegalAction, ValueError) as exc:
                self.server.send(conn, {"t": "reject", "reason": str(exc)})
                self._log(f"{conn.name}'s action was refused: {exc}", "info")
                return
            session.run_bots()
            self._log_new_events()
            self._turn_key = None
            self._progress()
        elif kind == "ready":
            # A guest saying "deal me in". The host still deals every hand, so
            # this is a courtesy: it shows up in the host's log.
            conn.ready = True
            self._log(f"{conn.name} is ready for the next hand.", "info")
        elif kind == "bye":
            conn.close()
            self.server.remove(conn)
            self.remote_left(conn)

    def remote_left(self, conn) -> None:
        session = self.app.session
        if session is None or conn.seat is None:
            return
        seat_index = conn.seat
        if seat_index >= len(session.seats):
            return
        seat = session.seats[seat_index]
        if not seat.is_human:
            return
        session.replace_seat_with_bot(seat_index)
        conn.seat = None
        self._log(f"{seat.name} dropped out; a bot is covering seat {seat_index}.", "win")
        if session.awaiting_human is False and session.hand is not None:
            session.run_bots()
        self.app.notify(f"{seat.name} disconnected - bots are covering.")
        self._progress()

    # -- clock ---------------------------------------------------------
    def tick_clock(self) -> None:
        session = self.app.session
        if session is None or session.hand is None:
            return
        if session.hand.street is Street.COMPLETE or not session.awaiting_human:
            self._turn_key = None
            return
        actor = session.active_human
        if actor is None:
            return
        key = (session.hand.hand_id, len(session.hand.events), actor.index)
        if key != self._turn_key:
            self._turn_key = key
            self._turn_started = time.monotonic()
            return
        limit = float(self.app.settings.get("action_clock") or 30)
        elapsed = time.monotonic() - self._turn_started
        if actor.index in (self._my_seats or ()) or elapsed < limit:
            return
        legal = session.legal_actions()
        action, amount = ("check", 0) if legal.can_check else ("fold", 0)
        try:
            session.apply_remote(actor.index, action, amount)
        except IllegalAction:
            return
        session.run_bots()
        self._log(f"{actor.name} ran out of time and {'checked' if action == 'check' else 'folded'}.", "info")
        self._turn_key = None
        self._progress()

    # -- broadcast -----------------------------------------------------
    def _broadcast(self) -> None:
        session = self.app.session
        if session is None:
            return
        if session.hands_played != self._announced_hand and session.hand is not None:
            self._announced_hand = session.hands_played
            self.server.broadcast(
                {
                    "t": "deal",
                    "hand_id": session.hands_played,
                    "deck_commit": session.hand.deck_commit,
                    "button": session.hand.button,
                }
            )
        for conn in self.server.players():
            if conn.seat is None:
                continue
            self.server.send(conn, state_message(session, conn))

    def _finish_hand(self) -> None:
        super()._finish_hand()
        session = self.app.session
        outcome = self._outcome
        if session is None or outcome is None:
            return
        self.server.broadcast(result_message(session, outcome))

    def _handle_busts(self) -> None:
        """Remote players are the host's guests: the house covers their stack.

        A prompt on the host's screen asking to buy in *somebody else's* chips
        would be nonsense, so remote seats are re-bought automatically and the
        host is simply told about it.
        """
        session = self.app.session
        if session is None:
            return
        for index in list(session.human_seats):
            if self._my_seats is not None and index not in self._my_seats:
                seat = session.seats[index]
                if seat.stack > 0 or seat.sitting_out:
                    continue
                holder = session.holder_for(index)
                if holder.guest and holder.bankroll.chips < session.level.buy_in:
                    holder.bankroll.chips = session.level.buy_in
                    holder.bankroll._record("top_up", session.level.buy_in, "LAN guest")
                taken = session.rebuy(index)
                if taken:
                    self._log(f"{seat.name} buys in again for {taken:,}", "info")
                else:
                    seat.sitting_out = True
                    self._log(f"{seat.name} is out of chips and sits out", "info")
        super()._handle_busts()

    # -- leaving -------------------------------------------------------
    def action_leave_table(self) -> None:
        session = self.app.session
        if session is None:
            self.app.pop_screen()
            return
        live = session.hand is not None and session.hand.street is not Street.COMPLETE
        if live:
            self.app.push_screen(
                ConfirmScreen(
                    "Close the hosted table?",
                    detail="The hand in progress is folded, everyone is disconnected, "
                    "and your chips come back to your bankroll.",
                ),
                lambda ok: self._close_everything() if ok else None,
            )
        else:
            self._close_everything()

    def _close_everything(self) -> None:
        self.server.broadcast(
            {"t": "bye", "reason": "the host closed the table"}
        )
        self.server.stop()
        if self.app.host_server is self.server:
            self.app.host_server = None
        self.app.leave_table()


# --------------------------------------------------------------------------
# joining
# --------------------------------------------------------------------------

class JoinScreen(BaseScreen):
    """Connect to somebody else's table."""

    def __init__(self, prefill: str = "") -> None:
        super().__init__()
        self.prefill = prefill
        self.client: TableClient | None = None
        self._deadline = 0.0
        self._joined = False
        self._error = ""
        self._poll: Timer | None = None

    def compose(self) -> ComposeResult:
        yield Static(
            f"[{theme.ACCENT}][b]Join a LAN table[/b][/]", id="panel-title"
        )
        with Center():
            with Vertical(id="menu-card"):
                yield Static(
                    f"[{theme.TEXT_DIM}]Paste the whole invitation, or type the "
                    f"room code if you can see the host's screen.[/]"
                )
                yield Input(
                    value=self.prefill,
                    placeholder="omapoker://192.168.1.42:8760/OMA-7K3QF-92  or  OMA-7K3QF-92",
                    id="join-input",
                )
                if not self.prefill:
                    yield Input(placeholder="host address, e.g. 192.168.1.42", id="join-host")
                    yield Input(value="8760", placeholder="port", id="join-port")
                yield Static("", id="join-status")
                yield Button("Join", id="join", variant="success")
                yield Button("Back", id="back")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#join-input", Input).focus()

    def on_unmount(self) -> None:
        self.cancel()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        identifier = event.button.id or ""
        if identifier == "join":
            self.attempt()
        elif identifier == "back":
            self.cancel()
            self.action_back()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.attempt()

    def attempt(self) -> None:
        if self.client is not None:
            return
        raw = self.query_one("#join-input", Input).value.strip()
        host = ""
        port = 8760
        typed_code = ""
        if raw:
            try:
                target = parse_join_string(raw)
            except ProtocolError as exc:
                self.status(f"[{theme.RED}]{exc}[/]")
                return
            host, port, typed_code = target.host, target.port, target.code
        if not host:
            boxes = list(self.query("#join-host", Input))
            if boxes:
                host = boxes[0].value.strip()
            ports = list(self.query("#join-port", Input))
            if ports:
                raw_port = ports[0].value.strip()
                try:
                    port = int(raw_port)
                except ValueError:
                    self.status(f"[{theme.RED}]Enter a port from 1 to 65535.[/]")
                    return
        if not 1 <= port <= 65535:
            self.status(f"[{theme.RED}]Enter a port from 1 to 65535.[/]")
            return
        if not host:
            self.status(f"[{theme.RED}]I need an address and a room code.[/]")
            return
        if not typed_code:
            self.status(f"[{theme.RED}]I need the room code.[/]")
            return

        player = self.app.player
        client = TableClient(player, timeout=5.0)
        self.status(f"[{theme.WARN}]Connecting to {host}:{port}...[/]")
        try:
            client.connect(host, port, typed_code)
        except OSError as exc:
            self.status(f"[{theme.RED}]Could not reach {host}:{port} ({exc}).[/]")
            return
        self.client = client
        self._deadline = time.monotonic() + 8.0
        self._poll = self.set_interval(0.2, self.pump)

    def stop_polling(self) -> None:
        """Hand the client over: the table screen reads it from now on."""
        poll, self._poll = self._poll, None
        if poll is not None:
            poll.stop()

    def pump(self) -> None:
        client = self.client
        if client is None:
            return
        for message in client.poll():
            kind = client.absorb(message)
            if kind == "reject":
                self.status(f"[{theme.RED}]{client.state.error}[/]")
                self.cancel()
                return
            if kind == "welcome" and client.state.seat is not None:
                self._joined = True
            if kind in ("welcome", "lobby") and client.state.joined and not self._joined:
                host = escape(client.state.host_name or "the host")
                self.status(
                    f"[{theme.ACCENT}]Joined {host}.[/]\n"
                    f"[{theme.TEXT}]Waiting for the host to start the table.[/]"
                )
            if kind == "state" and self._joined:
                self.enter_table()
                return
        if not client.connected:
            self.status(
                f"[{theme.RED}]{client.state.error or client.state.closed_reason}[/]"
            )
            self.cancel()
            return
        if time.monotonic() > self._deadline and not client.state.joined:
            self.status(
                f"[{theme.WARN}]No answer from the host. Check the room code and that "
                f"you are on the same network.[/]"
            )
            self.cancel()

    def enter_table(self) -> None:
        client = self.client
        self.client = None
        self.stop_polling()
        if client is None:
            return
        self.app.notify(
            f"Seated at seat {client.state.seat} on {client.state.host_name or 'the host'}'s table."
        )
        self.app.push_screen(RemoteTableScreen(client))

    def cancel(self) -> None:
        self.stop_polling()
        if self.client is not None:
            self.client.close("cancelled")
            self.client = None
        self.refresh()

    def status(self, text: str) -> None:
        try:
            self.query_one("#join-status", Static).update(text)
        except Exception:  # pragma: no cover - screen may be closing
            pass


class RemoteTableScreen(BaseScreen):
    """Somebody else's table, from one seat."""

    BINDINGS = [
        Binding("f", "fold", "Fold"),
        Binding("c", "check_call", "Check/Call"),
        Binding("r", "raise_mode", "Raise"),
        Binding("b", "bet_mode", "Bet"),
        Binding("x", "all_in", "All in", show=False),
        Binding("n", "ready", "Ready", show=False),
        Binding("q", "leave", "Leave table"),
        Binding("escape", "leave", "Leave"),
        Binding("enter", "confirm_amount", "Confirm", show=False),
        Binding("space", "music_toggle", "Play/pause", show=False),
        Binding("comma", "volume_down", "Vol -", show=False),
        Binding("full_stop", "volume_up", "Vol +", show=False),
    ]

    def __init__(self, client: TableClient) -> None:
        super().__init__()
        self.client = client
        self._pending = ""
        self._seat_count = 0
        self._logged = 0
        self._chip_text = "Off"
        self._levels = [0.0] * 12
        self._live = False
        self._phase = 0.0
        self._state: dict | None = None
        self._result: dict | None = None
        self._paint_tries = 0
        self._awaiting = False

    # -- layout --------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Horizontal(
            Static("", id="topbar-left"),
            MusicChip(id="music-chip-wrap"),
            id="topbar",
        )
        yield Vertical(
            Room(Vertical(id="ring"), id="room"),
            Horizontal(
                CoachPanel("", id="coach-panel"),
                LogPanel(id="log-panel"),
                id="bottom-rows",
            ),
            id="table-body",
        )
        yield ActionBar(id="actionbar")

    def on_mount(self) -> None:
        self.set_interval(0.2, self.pump)
        self.set_interval(MUSIC_INTERVAL, self._tick_music)
        self.set_interval(BAR_INTERVAL, self._tick_bars)
        if self.app.music is not None and self.app.music.visualiser is not None:
            self.app.music.visualiser.start()
        self._log(
            "Connected. The dealer is the host's machine; the coach lives on their screen.",
            "info",
        )
        # The lobby screen consumed the host's first state and result before it
        # handed the client over. Start from them: arriving at a blank table and
        # waiting for the next broadcast would look like the table was empty.
        if self.client.state.last_state:
            self.ingest_state(self.client.state.last_state)
        if self.client.state.last_result:
            self.ingest_result(self.client.state.last_result)
        self.refresh_view()
        self.pump()

    def on_unmount(self) -> None:
        self.client.close(self.client.state.closed_reason or "left the table")
        if self.app.music is not None and self.app.music.visualiser is not None:
            self.app.music.visualiser.stop()

    # -- network -------------------------------------------------------
    def pump(self) -> None:
        changed = False
        for message in self.client.poll():
            kind = self.client.absorb(message)
            if kind == "state":
                self.ingest_state(message)
                changed = True
            elif kind == "deal":
                self._log(
                    f"Hand {message.get('hand_id')} dealt. Deck committed to "
                    f"{str(message.get('deck_commit'))[:12]}...",
                    "street",
                )
            elif kind == "result":
                self.ingest_result(message)
                changed = True
            elif kind == "reject":
                self.app.notify(str(message.get("reason") or "the host refused"), severity="warning")
            elif kind == "bye":
                self.app.notify(
                    str(message.get("reason") or "the host left"), severity="error"
                )
                self.app.host_disconnected()
                return
        if not self.client.connected:
            self.app.notify(
                self.client.state.closed_reason or "Lost the connection.", severity="error"
            )
            self.app.host_disconnected()
            return
        if changed:
            self.refresh_view()

    def ingest_state(self, message: dict) -> None:
        # A fresh state means the host has caught up with whatever we sent.
        self._awaiting = False
        seats = message.get("seats") or []
        if len(seats) != self._seat_count:
            self._seat_count = len(seats)
            self.rebuild_ring(self._seat_count)
        events = message.get("events") or []
        for line in events[self._logged :]:
            self._log(str(line), "action")
        self._logged = len(events)
        self._state = message

    def ingest_result(self, message: dict) -> None:
        self._result = message
        board = " ".join(
            theme.card_markup(C.parse_card(text))
            for text in message.get("board") or []
            if isinstance(text, str)
        )
        net = (message.get("net") or {}).get(str(self.client.state.seat))
        if board:
            self._log(f"Board: {board}", "street")
        if net is not None:
            self._log(
                f"Hand finished {net:+,} for you", "win" if net > 0 else "info"
            )
        for index, cards in sorted((message.get("reveal") or {}).items()):
            shown = " ".join(
                theme.card_markup(C.parse_card(text))
                for text in cards
                if isinstance(text, str)
            )
            self._log(f"Seat {index} showed {shown}", "info")

    def _log(self, text: str, kind: str = "info") -> None:
        if not text:
            return
        try:
            self.query_one(LogPanel).add_event(text, kind)
        except Exception:  # pragma: no cover - the log is cosmetic
            pass

    def rebuild_ring(self, count: int) -> None:
        ring = self.query_one("#ring", Vertical)
        ring.remove_children()
        rows = []
        hero = self.client.state.seat or 0
        for row_number, row in enumerate(ring_rows(count, hero)):
            cells = []
            for cell in row:
                if cell is BOARD:
                    cells.append(PokerTable(id="poker-table"))
                elif isinstance(cell, int):
                    cells.append(SeatCard(cell, id=f"seat-{cell}"))
                else:
                    cells.append(Static("", classes="seat-spacer"))
            row_class = (
                "table-row"
                if BOARD in row
                else ("hero-row" if row_number == 2 else "outer-row")
            )
            rows.append(
                Horizontal(
                    Static("", classes="seat-fill"),
                    *cells,
                    Static("", classes="seat-fill"),
                    classes=f"ring-row {row_class}",
                )
            )
        ring.mount(*rows)

    # -- painting ------------------------------------------------------
    def refresh_view(self) -> None:
        message = self._state
        if not message:
            return
        seat = self.client.state.seat
        style = theme.cards_style(self.app.player.profile.cards_style)
        board = [C.parse_card(text) for text in message.get("board") or []]

        # Controls, status and coaching do not depend on the dynamically built
        # ring. Refresh them immediately so a newly joined player can never act
        # from stale buttons while the larger table finishes mounting.
        self.query_one("#topbar-left", Static).update(
            f"[{theme.ACCENT}]{message.get('level', 'table')}[/]  "
            f"[{theme.TEXT_DIM}]hand[/] [{theme.TEXT}]#{message.get('hand_id', 0)}[/]  "
            f"[{theme.TEXT_DIM}]pot[/] [{theme.ACCENT}]{message.get('pot', 0):,}[/]\n"
            f"[{theme.TEXT_DIM}]you are seat[/] [{theme.TEXT}]{seat}[/]  "
            f"[{theme.TEXT_DIM}]host[/] [{theme.TEXT}]{self.client.state.host_name or '?'}[/]  "
            f"[{theme.TEXT_DIM}]room[/] [{theme.TEXT}]{self.client.state.room_code}[/]"
        )
        self._refresh_actions(message)
        self._refresh_coach(message, board)

        # A ring that was just rebuilt has not been mounted yet: Textual paints
        # new widgets on the next refresh, so come back after it.
        try:
            self.query_one(BoardPanel)
            for entry in message.get("seats") or []:
                self.query_one(f"#seat-{entry.get('index', 0)}", SeatCard)
        except Exception:
            if self._paint_tries < 4:
                self._paint_tries += 1
                self.call_after_refresh(self.refresh_view)
            return
        self._paint_tries = 0

        for entry in message.get("seats") or []:
            index = entry.get("index", 0)
            try:
                card = self.query_one(f"#seat-{index}", SeatCard)
            except Exception:
                continue
            hole = [
                C.parse_card(text)
                for text in (entry.get("hole") or [])
                if isinstance(text, str)
            ]
            label = ""
            if index == seat and len(board) >= 3 and hole:
                label = hand_name_short(evaluate(list(hole) + list(board)))
            card.show(
                name=str(entry.get("name") or f"seat {index}"),
                stack=int(entry.get("stack") or 0),
                hole=hole,
                hidden=False,
                folded=bool(entry.get("folded")),
                all_in=bool(entry.get("all_in")),
                last_action=str(entry.get("last_action") or ""),
                dealer=message.get("button") == index,
                your_turn=message.get("to_act") == index,
                is_you=index == seat,
                style=style,
                hand_label=label,
                sitting_out=bool(entry.get("sitting_out")),
                prominent=index == seat,
            )

        try:
            self.query_one(BoardPanel).show(
                board,
                int(message.get("pot") or 0),
                message.get("street") or "preflop",
                style=style,
                current_bet=int(message.get("current_bet") or 0),
            )
        except Exception:  # pragma: no cover - ring not built yet
            pass

    def _refresh_actions(self, message: dict) -> None:
        bar = self.query_one(ActionBar)
        legal = legal_from_payload(message.get("legal"))
        mine = message.get("legal") is not None
        if not mine:
            self._pending = ""
            bar.set_legal(LegalActions(can_fold=False), enabled=False, note=self._idle_note(message))
            return
        bar.set_legal(legal, enabled=True, note="")
        if not self._pending:
            minimum = legal.min_raise_to if legal.can_raise else legal.min_bet
            bar.suggest(minimum)

    def _idle_note(self, message: dict) -> str:
        seat = self.client.state.seat
        if self._awaiting:
            return "Action sent - waiting for the host's table to catch up."
        actor = message.get("to_act")
        if actor is None:
            return "Waiting for the next hand."
        if actor == seat:
            return ""
        names = {s.get("index"): s.get("name") for s in message.get("seats") or []}
        return f"Waiting for {names.get(actor, 'the next player')}..."

    def _refresh_coach(self, message: dict, board: list[int]) -> None:
        panel = self.query_one(CoachPanel)
        seat = self.client.state.seat
        style = theme.cards_style(self.app.player.profile.cards_style)
        lines: list[str] = []
        if self._result is not None:
            net = (self._result.get("net") or {}).get(str(seat))
            if net is not None:
                lines.append(f"[{theme.ACCENT}]Last hand: {net:+,}[/]")
        entry = next(
            (s for s in message.get("seats") or [] if s.get("index") == seat), None
        )
        hole = [
            C.parse_card(text)
            for text in ((entry or {}).get("hole") or [])
            if isinstance(text, str)
        ]
        if hole and self.app.dial.enabled:
            lines.append(
                f"[{theme.TEXT}]You hold[/] "
                + " ".join(theme.card_markup(card, style) for card in hole)
            )
            if len(board) >= 3:
                lines.append(
                    f"[{theme.ACCENT}]{hand_name_short(evaluate(hole + board))}[/]"
                )
        lines.append(
            f"[{theme.TEXT_DIM}]The assist dial runs on the host's screen: their "
            f"coach reads the hand they can see. Your own cards stay yours.[/]"
        )
        if message.get("legal") is None:
            lines.append(f"[{theme.TEXT_DIM}]{self._idle_note(message)}[/]")
        panel.update("\n".join(lines))

    # -- music chip (the same chip as the offline table) ----------------
    def _tick_music(self) -> None:
        if self.app.music is None:
            return
        try:
            state = self.app.music.refresh()
        except Exception:  # pragma: no cover
            return
        self._chip_text = state.short(26) if state.title else state.source_label
        self.query_one(MusicChip).show(self._chip_text, self._levels, self._live)

    def _tick_bars(self) -> None:
        if self.app.music is None:
            return
        try:
            levels, live = self.app.music.levels(12)
        except Exception:  # pragma: no cover
            levels, live = [0.0] * 12, False
        if not live:
            self._phase += 0.25
            levels = idle_wave(12, self._phase)
        self._levels, self._live = levels, live
        self.query_one(MusicChip).show(self._chip_text, levels, live)

    def on_music_chip_open_panel(self, event) -> None:
        self.app.open_music()

    def on_music_chip_cycle_source(self, event: MusicChip.CycleSource) -> None:
        self.app.music.cycle_source()
        self._tick_music()

    def action_music_toggle(self) -> None:
        self.app.music.toggle_play()
        self.app.notify_playing()

    def action_volume_down(self) -> None:
        self.app.music.adjust_volume(-5)

    def action_volume_up(self) -> None:
        self.app.music.adjust_volume(5)

    # -- acting --------------------------------------------------------
    def _send(self, action: str, amount: int = 0) -> None:
        if (self._state or {}).get("legal") is None:
            self.app.notify("It is not your turn.", severity="warning")
            return
        if self._awaiting:
            # Already on its way. The host answers with a fresh state; pressing
            # again before then would just be refused at the other end.
            self.app.notify("Your action is already with the host.", severity="warning")
            return
        if not self.client.action(action, amount):
            self.app.notify("The connection is gone.", severity="error")
            return
        self._awaiting = True
        self._pending = ""
        self.query_one(ActionBar).clear_amount()
        self.query_one(ActionBar).blur_amount()

    def _legal(self) -> LegalActions:
        return legal_from_payload((self._state or {}).get("legal"))

    def action_fold(self) -> None:
        if self._legal().can_fold:
            self._send("fold")

    def action_check_call(self) -> None:
        legal = self._legal()
        if legal.can_check:
            self._send("check")
        elif legal.can_call:
            self._send("call", legal.call_amount)

    def action_raise_mode(self) -> None:
        self._begin_amount("raise")

    def action_bet_mode(self) -> None:
        self._begin_amount("bet")

    def action_all_in(self) -> None:
        legal = self._legal()
        if legal.can_all_in:
            self._send("raise" if legal.can_raise else "bet", legal.max_raise_to)

    def _begin_amount(self, kind: str) -> None:
        legal = self._legal()
        bar = self.query_one(ActionBar)
        if legal is None or not (legal.can_bet or legal.can_raise):
            self.app.notify("You cannot bet here.", severity="warning")
            return
        if kind == "bet" and not legal.can_bet:
            kind = "raise"
        if kind == "raise" and not legal.can_raise:
            kind = "bet"
        if self._pending == kind:
            self.action_confirm_amount()
            return
        self._pending = kind
        minimum = legal.min_bet if kind == "bet" else legal.min_raise_to
        pot = int((self._state or {}).get("pot") or 0)
        suggestion = max(minimum, min(legal.max_raise_to, pot + legal.call_amount))
        bar.suggest(suggestion or minimum)
        bar.focus_amount()
        self._refresh_actions(self._state or {})

    def action_confirm_amount(self) -> None:
        if not self._pending:
            return
        legal = self._legal()
        bar = self.query_one(ActionBar)
        amount = bar.amounts()
        minimum = legal.min_bet if self._pending == "bet" else legal.min_raise_to
        if amount <= 0:
            amount = minimum
        amount = max(minimum, min(amount, legal.max_raise_to))
        self._send(self._pending, amount)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "bet-input":
            self.action_confirm_amount()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        identifier = event.button.id or ""
        if identifier == "btn-fold":
            self.action_fold()
        elif identifier == "btn-call":
            self.action_check_call()
        elif identifier == "btn-raise":
            legal = self._legal()
            self._begin_amount("raise" if legal.can_raise else "bet")
        elif identifier in ("btn-min", "btn-half", "btn-pot", "btn-max"):
            legal = self._legal()
            bar = self.query_one(ActionBar)
            pot = int((self._state or {}).get("pot") or 0)
            if identifier == "btn-min":
                bar.suggest(legal.min_bet if legal.can_bet else legal.min_raise_to)
            elif identifier == "btn-half":
                bar.suggest(max(legal.min_raise_to, (pot + legal.call_amount) // 2))
            elif identifier == "btn-pot":
                bar.suggest(max(legal.min_raise_to, pot + legal.call_amount))
            else:
                bar.suggest(legal.max_raise_to)

    def action_leave(self) -> None:
        self.app.push_screen(
            ConfirmScreen(
                "Leave this table?",
                detail="The host will cover your seat with a bot.",
            ),
            self._leave_confirmed,
        )

    def action_ready(self) -> None:
        """Tell the host you are ready for the next hand."""
        if self.client.ready():
            self.app.notify("The host knows you are ready.")

    def _leave_confirmed(self, ok: bool | None) -> None:
        if ok:
            self.client.close("left the table")
            self.app.host_disconnected(from_client=True)


__all__ = [
    "HostScreen",
    "HostTableScreen",
    "JoinScreen",
    "RemoteTableScreen",
    "bind_remote_seats",
    "legal_from_payload",
    "legal_payload",
    "result_message",
    "state_message",
]
