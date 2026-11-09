"""Wire format for LAN tables.

One JSON object per line, UTF-8, nothing else. Deliberately boring: a telnet
client could play a hand if it tried hard enough, and nothing here needs a
dependency beyond the standard library.

Messages carry a ``t`` (type) field. Everything else is per-type. Unknown types
are ignored rather than fatal, so an older client survives a newer host.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from typing import Any, Iterable

#: Anything longer than this is not a poker message.
MAX_LINE = 64 * 1024

#: Bumped whenever a message changes shape incompatibly.
PROTOCOL_VERSION = 1


class ProtocolError(Exception):
    """A message did not make sense. The connection is dropped."""


# --------------------------------------------------------------------------
# framing
# --------------------------------------------------------------------------

def encode(message: dict[str, Any]) -> bytes:
    """One message, ready for the socket."""
    try:
        text = json.dumps(message, separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError) as exc:  # pragma: no cover - programming error
        raise ProtocolError(f"message is not JSON-serialisable: {exc}") from exc
    return text.encode("utf-8") + b"\n"


def decode(line: bytes | str) -> dict[str, Any]:
    """One line from the socket, as a message."""
    if isinstance(line, bytes):
        try:
            text = line.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProtocolError("message was not UTF-8") from exc
    else:
        text = line
    text = text.strip()
    if not text:
        raise ProtocolError("empty message")
    try:
        message = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"message was not JSON: {exc.msg}") from exc
    if not isinstance(message, dict) or "t" not in message:
        raise ProtocolError("every message needs a type")
    return message


@dataclass
class LineReader:
    """Reassembles newline-framed messages from arbitrary TCP chunks."""

    buffer: bytearray = field(default_factory=bytearray)
    #: Set when the peer sent something absurd; the caller should disconnect.
    overflow: bool = False

    def feed(self, chunk: bytes) -> list[dict[str, Any]]:
        if not chunk:
            return []
        self.buffer.extend(chunk)
        if len(self.buffer) > MAX_LINE and b"\n" not in self.buffer:
            self.overflow = True
            self.buffer.clear()
            return []
        messages: list[dict[str, Any]] = []
        while True:
            index = self.buffer.find(b"\n")
            if index < 0:
                break
            line = bytes(self.buffer[:index])
            del self.buffer[: index + 1]
            if len(line) > MAX_LINE:
                self.overflow = True
                continue
            try:
                messages.append(decode(line))
            except ProtocolError:
                # One bad line is not worth a whole connection, unless it is
                # the first one - the caller decides based on the count.
                messages.append({"t": "_malformed"})
        return messages


def require(message: dict[str, Any], *keys: str) -> dict[str, Any]:
    """Assert a message carries what its type promises."""
    missing = [key for key in keys if key not in message]
    if missing:
        raise ProtocolError(
            f"{message.get('t', '?')} message is missing {', '.join(missing)}"
        )
    return message


def is_action(message: dict[str, Any]) -> bool:
    return message.get("t") == "action"


# --------------------------------------------------------------------------
# room codes
# --------------------------------------------------------------------------
#
# Same shape as an identity code (and the same Crockford-style alphabet, minus
# I, L, O and U) so a room code can be read down the phone without anyone
# writing "was that an oh or a zero?".

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_PREFIX = "OMA"
_BODY = 5
_SUFFIX = 2


def new_room_code(rng: secrets.SystemRandom | None = None) -> str:
    """A fresh room code, e.g. ``OMA-7K3QF-92``."""
    picker = rng or secrets.SystemRandom()
    body = "".join(picker.choice(_ALPHABET) for _ in range(_BODY))
    suffix = "".join(picker.choice("0123456789") for _ in range(_SUFFIX))
    return f"{_PREFIX}-{body}-{suffix}"


def normalise_room_code(code: str) -> str | None:
    """Accept a code typed with spaces, lowercase or missing hyphens.

    The prefix is stripped *before* filtering to the code alphabet, because the
    alphabet omits O - and "OMA" starts with one.
    """
    if not isinstance(code, str):
        return None
    cleaned = code.strip().upper().replace(" ", "").replace("-", "").replace("_", "")
    if cleaned.startswith(_PREFIX):
        cleaned = cleaned[len(_PREFIX) :]
    cleaned = "".join(c for c in cleaned if c in _ALPHABET)
    if len(cleaned) != _BODY + _SUFFIX:
        return None
    return f"{_PREFIX}-{cleaned[:_BODY]}-{cleaned[_BODY:]}"


def room_codes_match(typed: str, actual: str) -> bool:
    left = normalise_room_code(typed)
    right = normalise_room_code(actual)
    return left is not None and left == right


# --------------------------------------------------------------------------
# join strings
# --------------------------------------------------------------------------

SCHEME = "omapoker"


def join_string(host: str, port: int, code: str) -> str:
    """The whole invitation in one line: scheme, address, room."""
    return f"{SCHEME}://{host}:{int(port)}/{code}"


@dataclass(frozen=True)
class JoinTarget:
    """Where to connect, and which room to ask for."""

    host: str = ""
    port: int = 8760
    code: str = ""

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"

    def as_string(self) -> str:
        return join_string(self.host, self.port, self.code)

    @property
    def complete(self) -> bool:
        return bool(self.host and self.code)


def parse_join_string(text: str) -> JoinTarget:
    """Parse anything a player might paste.

    Accepts the full ``omapoker://host:port/CODE`` invitation, a bare
    ``host:port``, a bare room code, or a mixture. Raises :class:`ProtocolError`
    when there is nothing usable in it.
    """
    raw = (text or "").strip()
    if not raw:
        raise ProtocolError("nothing to join: type a room code or an invitation")

    host = ""
    port = 8760
    code = ""

    if "://" in raw:
        scheme, _, rest = raw.partition("://")
        if scheme.lower() not in (SCHEME, "tcp"):
            raise ProtocolError(f"{scheme} is not an OmaPoker invitation")
        raw = rest

    # A trailing /CODE is the room; everything before it is the address.
    if "/" in raw:
        raw, _, tail = raw.partition("/")
        code = tail.strip()

    if raw:
        if raw.count(":") == 1:
            maybe_host, _, maybe_port = raw.partition(":")
            if maybe_port.isdigit():
                host, port = maybe_host.strip(), int(maybe_port)
            else:
                # "host:OMA-..." with the code stuck on the end.
                code = code or maybe_port.strip()
                host = maybe_host.strip()
        elif raw.upper().startswith(_PREFIX):
            code = code or raw
        else:
            host = raw

    if not code and not host:
        raise ProtocolError("that does not look like an invitation or a room code")

    normalised = normalise_room_code(code) if code else None
    if code and normalised is None:
        raise ProtocolError(f"{code!r} is not an OmaPoker room code")
    if not 1 <= port <= 65535:
        raise ProtocolError(f"{port} is not a usable port")
    return JoinTarget(host=host, port=port, code=normalised or "")


# --------------------------------------------------------------------------
# addresses
# --------------------------------------------------------------------------

def local_addresses() -> list[str]:
    """IPv4 addresses a friend on the same network could reach.

    No traffic is sent: connecting a UDP socket only asks the kernel which
    interface it *would* use.
    """
    import socket

    found: list[str] = []
    for probe in ("10.255.255.255", "192.168.255.255"):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect((probe, 1))
            address = sock.getsockname()[0]
            if address and address not in found:
                found.append(address)
        except OSError:
            continue
        finally:
            sock.close()
    if not found:
        found.append("127.0.0.1")
    return found


def pick_address(addresses: Iterable[str] | None = None) -> str:
    """The most likely reachable address, preferring a real LAN address."""
    candidates = list(addresses if addresses is not None else local_addresses())
    for address in candidates:
        if not address.startswith("127."):
            return address
    return candidates[0] if candidates else "127.0.0.1"


__all__ = [
    "JoinTarget",
    "LineReader",
    "MAX_LINE",
    "PROTOCOL_VERSION",
    "ProtocolError",
    "SCHEME",
    "decode",
    "encode",
    "is_action",
    "join_string",
    "local_addresses",
    "new_room_code",
    "normalise_room_code",
    "parse_join_string",
    "pick_address",
    "require",
    "room_codes_match",
]
