"""The joining side of a LAN table.

One socket and one reader thread. The reader thread does exactly two things:
answer a challenge (which needs the profile's private key and nothing else), and
hand everything else to the interface through a queue.

The interface never blocks on the network: :meth:`TableClient.poll` is
non-blocking, and :meth:`TableClient.action` is a small write that either lands
or does not. A dropped connection shows up as a ``bye`` message rather than an
exception in the middle of a hand.
"""

from __future__ import annotations

import queue
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from ..data import PlayerData
from .protocol import PROTOCOL_VERSION, LineReader, encode


@dataclass
class ClientState:
    """What the host has told us, and what we told it."""

    connected: bool = False
    joined: bool = False
    seat: int | None = None
    host_name: str = ""
    room_code: str = ""
    players: list[dict[str, Any]] = field(default_factory=list)
    last_state: dict[str, Any] | None = None
    last_result: dict[str, Any] | None = None
    deck_commit: str = ""
    error: str = ""
    closed_reason: str = ""


class TableClient:
    """A player's connection to somebody else's table."""

    def __init__(self, player: PlayerData, *, timeout: float = 6.0) -> None:
        self.player = player
        self.timeout = float(timeout)
        self.host = ""
        self.port = 0
        self.room_code = ""
        self.sock: socket.socket | None = None
        self._reader: threading.Thread | None = None
        self._inbox: queue.Queue[dict[str, Any]] = queue.Queue()
        self._send_lock = threading.Lock()
        self._reader_state = LineReader()
        self.state = ClientState()

    # -- lifecycle -----------------------------------------------------
    def connect(self, host: str, port: int, room_code: str) -> None:
        """Connect and say hello. Raises on failure; the caller shows why."""
        if self.state.connected:
            return
        self.host, self.port, self.room_code = host, int(port), room_code
        sock = socket.create_connection((host, int(port)), timeout=self.timeout)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(None)
        self.sock = sock
        self.state.connected = True
        self._reader = threading.Thread(
            target=self._read_loop, name="omapoker-client", daemon=True
        )
        self._reader.start()
        identity = self.player.profile.identity
        self.send(
            {
                "t": "hello",
                "version": PROTOCOL_VERSION,
                "name": self.player.profile.name,
                "code": identity.code,
                "public": identity.public_hex,
                "room": self.room_code,
            }
        )

    def close(self, reason: str = "you left the table") -> None:
        if not self.state.closed_reason:
            self.state.closed_reason = reason
        if self.state.connected:
            self.send({"t": "bye", "reason": reason})
        self.state.connected = False
        if self.sock is not None:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    @property
    def connected(self) -> bool:
        return self.state.connected

    # -- io ------------------------------------------------------------
    def send(self, message: dict[str, Any]) -> bool:
        if self.sock is None:
            return False
        try:
            payload = encode(message)
        except Exception:  # pragma: no cover - defensive
            return False
        try:
            with self._send_lock:
                self.sock.sendall(payload)
        except OSError:
            self.state.connected = False
            return False
        return True

    def poll(self, limit: int = 64) -> list[dict[str, Any]]:
        """Everything the host has said since last time. Never blocks."""
        messages: list[dict[str, Any]] = []
        for _ in range(limit):
            try:
                messages.append(self._inbox.get_nowait())
            except queue.Empty:
                break
        return messages

    def action(self, action: str, amount: int = 0) -> bool:
        return self.send({"t": "action", "action": str(action), "amount": int(amount)})

    def ready(self) -> bool:
        return self.send({"t": "ready", "at": time.time()})

    def sit_out(self, value: bool = True) -> bool:
        return self.send({"t": "sitting_out", "value": bool(value)})

    # -- reader thread -------------------------------------------------
    def _read_loop(self) -> None:
        sock = self.sock
        assert sock is not None
        while self.state.connected:
            try:
                chunk = sock.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            for message in self._reader_state.feed(chunk):
                if self._handle_immediately(message):
                    continue
                self._inbox.put(message)
            if self._reader_state.overflow:
                self._inbox.put({"t": "reject", "reason": "message too large"})
                break
        self.state.connected = False
        if not self.state.closed_reason:
            self.state.closed_reason = "the host disconnected"
        self._inbox.put({"t": "_closed"})

    def _handle_immediately(self, message: dict[str, Any]) -> bool:
        """The bits that must not wait for the UI thread."""
        kind = message.get("t")
        if kind == "challenge":
            nonce_hex = str(message.get("nonce") or "")
            try:
                nonce = bytes.fromhex(nonce_hex)
            except ValueError:
                self.send({"t": "auth", "signature": ""})
                return True
            signature = self.player.profile.identity.sign(nonce)
            self.send({"t": "auth", "signature": signature})
            return True
        if kind == "ping":
            self.send({"t": "pong", "at": message.get("at")})
            return True
        if kind == "pong":
            return True
        return False

    # -- bookkeeping used by the interface -----------------------------
    def absorb(self, message: dict[str, Any]) -> str:
        """Fold one host message into :attr:`state`. Returns its type."""
        kind = str(message.get("t") or "")
        if kind == "welcome":
            self.state.joined = True
            self.state.seat = message.get("seat")
            self.state.host_name = str(message.get("host") or "")
            self.state.room_code = str(message.get("code") or self.room_code)
        elif kind == "lobby":
            self.state.players = list(message.get("players") or [])
            self.state.seat = message.get("seat", self.state.seat)
        elif kind == "state":
            self.state.last_state = message
        elif kind == "result":
            self.state.last_result = message
        elif kind == "deal":
            self.state.deck_commit = str(message.get("deck_commit") or "")
        elif kind == "reject":
            self.state.error = str(message.get("reason") or "the host said no")
            self.state.closed_reason = self.state.error
        elif kind == "bye":
            self.state.closed_reason = str(message.get("reason") or "the host closed the table")
            self.state.connected = False
        elif kind == "_closed":
            self.state.connected = False
        return kind


__all__ = ["ClientState", "TableClient"]
