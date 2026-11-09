"""LAN play: room codes, a host that runs the authoritative table, and clients.

The engine never imports any of this. A table with one player on a machine with
no network at all behaves exactly like the offline game, because the network is
a transport that happens to be attached to a session, not a part of one.

Whoever hosts is the dealer: their machine owns the deck, the stacks and the
clock. Everyone else sends actions and receives the public state, plus their own
hole cards and nobody else's.
"""

from .protocol import (
    SCHEME,
    JoinTarget,
    LineReader,
    MAX_LINE,
    PROTOCOL_VERSION,
    ProtocolError,
    join_string,
    local_addresses,
    new_room_code,
    normalise_room_code,
    parse_join_string,
    pick_address,
    room_codes_match,
)

__all__ = [
    "JoinTarget",
    "LineReader",
    "MAX_LINE",
    "PROTOCOL_VERSION",
    "ProtocolError",
    "SCHEME",
    "join_string",
    "local_addresses",
    "new_room_code",
    "normalise_room_code",
    "parse_join_string",
    "pick_address",
    "room_codes_match",
]
