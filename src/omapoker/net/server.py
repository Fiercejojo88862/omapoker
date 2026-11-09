"""The host side of a LAN table.

Threading model, which is the whole point of this module:

* one accept thread and one reader thread per connection, and nothing else off
  the UI thread,
* reader threads never touch the game. They decode a line, drop it on a queue,
  and go back to reading,
* the interface drains that queue on a timer and applies the actions itself.

So every mutation of the engine still happens on the one thread that owns it,
which is why LAN play cannot corrupt a hand even if a client misbehaves. A
misbehaving client can only send illegal *actions*, and the engine already
refuses those.

Identity is trust-on-first-use: a code is checked against the public key that
came with it, and a signed nonce proves the same key holder came back. It is not
a certificate authority and the interface says so.
"""

from __future__ import annotations

import queue
import secrets
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any

from .protocol import (
    PROTOCOL_VERSION,
    LineReader,
    ProtocolError,
    encode,
    local_addresses,
    normalise_room_code,
    pick_address,
    room_codes_match,
)


@dataclass
class Event:
    """Something the host has to deal with, handed to the UI thread."""

    kind: str  # "join" | "message" | "left"
    conn: "ClientConn | None" = None
    message: dict[str, Any] | None = None


class ClientConn:
    """One connected socket, plus what we know about who is behind it."""

    _next_id = 0

    def __init__(self, sock: socket.socket, address: tuple[str, int], server) -> None:
        ClientConn._next_id += 1
        self.id = ClientConn._next_id
        self.sock = sock
        self.address = address
        self.server = server
        self.created = time.monotonic()
        self.reader = LineReader()
        self.alive = True

        # Filled in by the host once the identity handshake completes.
        self.name = ""
        self.profile_id = ""
        self.public_hex = ""
        self.verified = False
        self.seat: int | None = None
        self.nonce: bytes = b""
        self.ready = False
        self.is_host = False

        self._send_lock = threading.Lock()
        self._closed = False

    # -- identity ------------------------------------------------------
    @property
    def identified(self) -> bool:
        return bool(self.name)

    @property
    def label(self) -> str:
        if not self.name:
            return f"unidentified ({self.address[0]})"
        return f"{self.name} ({'verified' if self.verified else 'unverified label'})"

    # -- io ------------------------------------------------------------
    def send(self, message: dict[str, Any]) -> bool:
        """Queue one message. Never raises; False means the peer is gone."""
        if not self.alive:
            return False
        try:
            payload = encode(message)
        except ProtocolError:
            return True
        try:
            with self._send_lock:
                self.sock.sendall(payload)
        except OSError:
            self.alive = False
            return False
        return True

    def close(self, reason: str = "", *, abort: bool = False) -> None:
        """Hang up.

        Plain ``close()`` is the polite path: whatever the peer already sent is
        read off the socket first, then the kernel flushes whatever we have
        queued (the goodbye) and sends FIN.

        Reading first matters. Closing a socket with an unread receive queue
        makes the kernel answer with RST instead of flushing, which throws away
        exactly the message we just queued - so every guest would be told "the
        host disconnected" rather than why.

        ``abort`` shuts both directions down instead, which is what we want for
        a client we are kicking out rather than letting go of.
        """
        if self._closed:
            return
        self._closed = True
        self.alive = False
        if abort:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        else:
            self._drain()
        try:
            self.sock.close()
        except OSError:
            pass

    def _drain(self) -> None:
        """Consume anything the peer has already sent, without blocking."""
        try:
            self.sock.settimeout(0.0)
        except OSError:
            return
        for _ in range(64):
            try:
                if not self.sock.recv(4096):
                    break
            except (BlockingIOError, socket.timeout):
                break
            except OSError:
                break

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ClientConn {self.id} {self.name or self.address[0]}>"


class TableServer:
    """Accepts players, collects messages, sends broadcasts."""

    def __init__(
        self,
        room_code: str,
        host_name: str = "OmaPoker table",
        *,
        host: str = "0.0.0.0",
        port: int = 8760,
        max_players: int = 8,
    ) -> None:
        self.room_code = room_code
        self.host_name = host_name
        self.bind_host = host
        self.requested_port = int(port)
        self.max_players = int(max_players)

        self.port = 0
        self.running = False
        self._listener: socket.socket | None = None
        self._events: queue.Queue[Event] = queue.Queue()
        self._accept_thread: threading.Thread | None = None
        self._connections: list[ClientConn] = []
        self._lock = threading.Lock()

    # -- lifecycle -----------------------------------------------------
    def start(self) -> int:
        """Bind and listen. Returns the port actually bound."""
        if self.running:
            return self.port
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.bind_host, self.requested_port))
        listener.listen(16)
        listener.settimeout(0.5)
        self._listener = listener
        self.port = listener.getsockname()[1]
        self.running = True
        self._accept_thread = threading.Thread(
            target=self._accept_loop, name="omapoker-accept", daemon=True
        )
        self._accept_thread.start()
        return self.port

    def stop(self) -> None:
        # Say goodbye to everyone *before* the reader threads are told to pack
        # up: a reader that exits first closes its socket, and a socket that is
        # already closed cannot deliver the reason the table went away.
        connections = self.connections
        for conn in connections:
            conn.send({"t": "bye", "reason": "the host closed the table"})
        if connections:
            # A moment for the goodbyes to reach the wire. Closing a socket
            # with unread data in its receive queue makes the kernel send an
            # RST, which would throw away exactly the message we just queued.
            time.sleep(0.05)

        self.running = False
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
            self._listener = None
        for conn in connections:
            conn.close()
        with self._lock:
            self._connections.clear()
        if self._accept_thread is not None:
            self._accept_thread.join(timeout=1.0)
            self._accept_thread = None

    # -- connections ---------------------------------------------------
    @property
    def connections(self) -> list[ClientConn]:
        with self._lock:
            return list(self._connections)

    def players(self) -> list[ClientConn]:
        return [c for c in self.connections if c.identified]

    def open_connections(self) -> int:
        return len(self.connections)

    @property
    def full(self) -> bool:
        return len(self.players()) >= self.max_players

    def can_accept(self) -> bool:
        """One slot is reserved for the host, so players cap out one early."""
        return len(self.connections) < self.max_players

    def remove(self, conn: ClientConn) -> None:
        with self._lock:
            if conn in self._connections:
                self._connections.remove(conn)

    def send(self, conn: ClientConn, message: dict[str, Any]) -> None:
        if not conn.send(message):
            self._events.put(Event("left", conn))

    def broadcast(self, message: dict[str, Any], *, skip: ClientConn | None = None) -> None:
        for conn in self.connections:
            if conn is skip:
                continue
            if not conn.send(message):
                self._events.put(Event("left", conn))

    # -- events --------------------------------------------------------
    def poll(self, limit: int = 64) -> list[Event]:
        events: list[Event] = []
        for _ in range(limit):
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                break
        return events

    def kick_idle(self, age: float = 20.0) -> list[ClientConn]:
        """Drop sockets that connected and never said who they are."""
        now = time.monotonic()
        stale = [
            conn
            for conn in self.connections
            if not conn.identified and now - conn.created > age
        ]
        for conn in stale:
            self.send(conn, {"t": "reject", "reason": "no hello, no seat"})
            conn.close()
            self.remove(conn)
            self._events.put(Event("left", conn))
        return stale

    # -- addresses -----------------------------------------------------
    def join_string(self, address: str | None = None) -> str:
        from .protocol import join_string

        return join_string(address or pick_address(local_addresses()), self.port, self.room_code)

    def accepts(self, typed_code: str) -> bool:
        return room_codes_match(typed_code, self.room_code)

    # -- internals -----------------------------------------------------
    def _accept_loop(self) -> None:
        while self.running and self._listener is not None:
            try:
                sock, address = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            if not self.can_accept():
                try:
                    sock.sendall(
                        encode({"t": "reject", "reason": "the table is full"})
                    )
                except OSError:
                    pass
                sock.close()
                continue
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            conn = ClientConn(sock, address, self)
            with self._lock:
                self._connections.append(conn)
            self._events.put(Event("join", conn))
            threading.Thread(
                target=self._read_loop,
                args=(conn,),
                name=f"omapoker-read-{conn.id}",
                daemon=True,
            ).start()

    def _read_loop(self, conn: ClientConn) -> None:
        sock = conn.sock
        while self.running and conn.alive:
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            for message in conn.reader.feed(chunk):
                kind = message.get("t")
                if kind == "ping":
                    conn.send({"t": "pong", "at": message.get("at")})
                    continue
                if kind == "pong":
                    continue
                if kind == "_malformed":
                    self.send(conn, {"t": "reject", "reason": "unreadable message"})
                    continue
                self._events.put(Event("message", conn, message))
            if conn.reader.overflow:
                self.send(conn, {"t": "reject", "reason": "message too large"})
                break
        conn.close()
        self.remove(conn)
        self._events.put(Event("left", conn))


# --------------------------------------------------------------------------
# handshake helpers (used by the interface, kept here so they are testable)
# --------------------------------------------------------------------------

def make_challenge() -> bytes:
    return secrets.token_bytes(16)


def challenge_message(nonce: bytes) -> dict[str, Any]:
    return {"t": "challenge", "nonce": nonce.hex(), "version": PROTOCOL_VERSION}


def hello_message(name: str, code: str, public_hex: str, room: str = "") -> dict[str, Any]:
    return {
        "t": "hello",
        "version": PROTOCOL_VERSION,
        "name": name,
        "code": code,
        "public": public_hex,
        "room": room,
    }


def auth_message(signature_hex: str) -> dict[str, Any]:
    return {"t": "auth", "signature": signature_hex}


@dataclass
class Verification:
    """The result of checking a hello + auth pair."""

    ok: bool
    reason: str = ""
    verified: bool = False

    @property
    def label(self) -> str:
        if not self.ok:
            return self.reason or "rejected"
        return "verified key" if self.verified else "unverified label"


def check_hello(message: dict[str, Any], expected_code: str) -> Verification:
    """Everything that can be checked before the nonce round trip."""
    from ..data.profiles import code_matches_key, crypto_available

    try:
        name = str(message.get("name") or "").strip()[:24]
        code = str(message.get("code") or "")
        public_hex = str(message.get("public") or "")
    except Exception:  # pragma: no cover - defensive
        return Verification(False, "unreadable hello")
    if not name:
        return Verification(False, "no name given")
    version = message.get("version")
    if version != PROTOCOL_VERSION:
        return Verification(False, f"protocol version {version} is not {PROTOCOL_VERSION}")
    if not expected_code:
        return Verification(False, "the host has no room code")
    # The room code the guest typed is the guest list: no code, no seat.
    if normalise_room_code(str(message.get("room") or "")) != normalise_room_code(
        expected_code
    ):
        return Verification(False, "that is not this table's room code")
    if not code_matches_key(code, public_hex):
        return Verification(False, "the identity code does not match the key")
    return Verification(True, verified=crypto_available())


def check_auth(
    conn: ClientConn, message: dict[str, Any], nonce: bytes
) -> Verification:
    """The nonce round trip: proves the same key holder answered."""
    from ..data.profiles import verify_signature

    signature = str(message.get("signature") or "")
    if not signature:
        return Verification(False, "no signature", verified=False)
    if not verify_signature(conn.public_hex, nonce, signature):
        return Verification(False, "the signature did not check out")
    return Verification(True, verified=True)


__all__ = [
    "ClientConn",
    "Event",
    "TableServer",
    "Verification",
    "auth_message",
    "challenge_message",
    "check_auth",
    "check_hello",
    "hello_message",
    "make_challenge",
]
