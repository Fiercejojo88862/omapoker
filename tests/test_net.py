"""LAN play: room codes, framing, the identity handshake, and a real socket.

These tests do open sockets, but only on 127.0.0.1 with port 0 (whatever the
kernel hands out), so they are fast and cannot collide with anything running on
the machine. No external host is ever contacted.
"""

from __future__ import annotations

import socket
import time

import pytest

from omapoker.data import DataStore
from omapoker.data.profiles import crypto_available
from omapoker.net import (
    ProtocolError,
    join_string,
    local_addresses,
    new_room_code,
    normalise_room_code,
    parse_join_string,
    pick_address,
    room_codes_match,
)
from omapoker.net.client import TableClient
from omapoker.net.protocol import LineReader, decode, encode
from omapoker.net.server import (
    TableServer,
    challenge_message,
    check_auth,
    check_hello,
    make_challenge,
)


# --------------------------------------------------------------------------
# room codes and invitations
# --------------------------------------------------------------------------

def test_room_codes_look_like_room_codes():
    code = new_room_code()
    assert code.startswith("OMA-")
    assert len(code) == 12
    body, _, suffix = code[4:].partition("-")
    assert len(body) == 5 and len(suffix) == 2
    assert suffix.isdigit()
    # No I, L, O or U anywhere: they are the letters people mistype.
    assert not set(code[4:]) & set("ILOU")


def test_room_codes_are_not_all_the_same():
    codes = {new_room_code() for _ in range(200)}
    assert len(codes) > 190


@pytest.mark.parametrize(
    "typed",
    [
        "OMA-7K3QF-92",
        "oma-7k3qf-92",
        "oma7k3qf92",
        "7K3QF92",
        "OMA 7K3QF 92",
        "OMA_7K3QF_92",
        "  OMA-7K3QF-92  ",
    ],
)
def test_room_codes_survive_being_typed(typed):
    assert normalise_room_code(typed) == "OMA-7K3QF-92"


@pytest.mark.parametrize("bad", ["", "nope", "OMA-7K3Q-92", "1234567890123456", None])
def test_junk_is_not_a_room_code(bad):
    assert normalise_room_code(bad) is None


def test_room_codes_match_across_formats():
    assert room_codes_match("7k3qf92", "OMA-7K3QF-92")
    assert not room_codes_match("OMA-7K3QF-93", "OMA-7K3QF-92")
    assert not room_codes_match("rubbish", "OMA-7K3QF-92")


def test_invitations_round_trip():
    text = join_string("192.168.1.42", 8760, "OMA-7K3QF-92")
    assert text == "omapoker://192.168.1.42:8760/OMA-7K3QF-92"
    target = parse_join_string(text)
    assert target.host == "192.168.1.42"
    assert target.port == 8760
    assert target.code == "OMA-7K3QF-92"
    assert target.complete
    assert target.as_string() == text


@pytest.mark.parametrize(
    "text,host,port,code",
    [
        ("192.168.1.42:9000/OMA-7K3QF-92", "192.168.1.42", 9000, "OMA-7K3QF-92"),
        ("192.168.1.42:8760", "192.168.1.42", 8760, ""),
        ("OMA-7K3QF-92", "", 8760, "OMA-7K3QF-92"),
        ("oma-7k3qf-92", "", 8760, "OMA-7K3QF-92"),
        ("omapoker://10.0.0.5/OMA-7K3QF-92", "10.0.0.5", 8760, "OMA-7K3QF-92"),
        ("10.0.0.5:8760/7k3qf92", "10.0.0.5", 8760, "OMA-7K3QF-92"),
    ],
)
def test_join_strings_are_forgiving(text, host, port, code):
    target = parse_join_string(text)
    assert (target.host, target.port, target.code) == (host, port, code)


@pytest.mark.parametrize("bad", ["", "   ", "http://example.com/x", "192.168.1.42:99999"])
def test_join_strings_reject_nonsense(bad):
    with pytest.raises(ProtocolError):
        parse_join_string(bad)


def test_local_addresses_prefers_something_reachable():
    addresses = local_addresses()
    assert addresses
    assert all(isinstance(a, str) and a for a in addresses)
    assert pick_address(["127.0.0.1"]) == "127.0.0.1"
    assert pick_address(["127.0.0.1", "192.168.1.5"]) == "192.168.1.5"
    assert pick_address([]) == "127.0.0.1"


# --------------------------------------------------------------------------
# framing
# --------------------------------------------------------------------------

def test_messages_are_one_line_of_json():
    payload = encode({"t": "hello", "name": "Joseph"})
    assert payload.endswith(b"\n")
    assert payload.count(b"\n") == 1
    assert decode(payload) == {"t": "hello", "name": "Joseph"}


def test_the_reader_reassembles_split_messages():
    reader = LineReader()
    first = reader.feed(b'{"t":"one"}\n{"t":"tw')
    assert first == [{"t": "one"}]
    second = reader.feed(b'o"}\n')
    assert second == [{"t": "two"}]


def test_the_reader_accepts_several_messages_at_once():
    reader = LineReader()
    messages = reader.feed(b'{"t":"a"}\n{"t":"b"}\n{"t":"c"}\n')
    assert [m["t"] for m in messages] == ["a", "b", "c"]


def test_junk_lines_do_not_bring_down_the_reader():
    reader = LineReader()
    messages = reader.feed(b"not json\n{\"t\":\"ok\"}\n")
    assert messages[0]["t"] == "_malformed"
    assert messages[1]["t"] == "ok"
    assert not reader.overflow


def test_an_enormous_line_is_flagged_rather_than_buffered():
    reader = LineReader()
    reader.feed(b"x" * (70 * 1024))
    assert reader.overflow
    assert not reader.buffer


@pytest.mark.parametrize("bad", [b"", b"\n", b"[]", b"{}", b'"just a string"'])
def test_decode_rejects_things_that_are_not_messages(bad):
    with pytest.raises(ProtocolError):
        decode(bad)


# --------------------------------------------------------------------------
# identity handshake
# --------------------------------------------------------------------------

def make_player(tmp_path, name="Joseph"):
    store = DataStore(tmp_path / "config")
    profile = store.create_profile(name, "casual")
    return store.player(profile.id)


@pytest.mark.skipif(not crypto_available(), reason="needs the cryptography package")
def test_a_hello_with_the_right_code_is_accepted(tmp_path):
    player = make_player(tmp_path, "Joseph")
    identity = player.profile.identity
    hello = {
        "t": "hello",
        "version": 1,
        "name": "Joseph",
        "code": identity.code,
        "public": identity.public_hex,
        "room": "OMA-7K3QF-92",
    }
    verdict = check_hello(hello, "OMA-7K3QF-92")
    assert verdict.ok
    assert verdict.verified


def test_a_hello_with_somebody_elses_code_is_rejected(tmp_path):
    player = make_player(tmp_path, "Joseph")
    other = make_player(tmp_path, "Natalie")
    hello = {
        "t": "hello",
        "version": 1,
        "name": "Sneaky",
        "code": other.profile.identity.code,
        "public": player.profile.identity.public_hex,
        "room": "OMA-7K3QF-92",
    }
    verdict = check_hello(hello, "OMA-7K3QF-92")
    assert not verdict.ok
    assert "does not match" in verdict.reason


def test_a_hello_without_a_name_or_with_the_wrong_version_is_rejected(tmp_path):
    player = make_player(tmp_path, "Joseph")
    identity = player.profile.identity
    good = {
        "t": "hello",
        "version": 1,
        "name": "Joseph",
        "code": identity.code,
        "public": identity.public_hex,
        "room": "OMA-7K3QF-92",
    }
    assert check_hello(good, "OMA-7K3QF-92").ok
    assert not check_hello({**good, "name": "  "}, "OMA-7K3QF-92").ok
    assert not check_hello({**good, "version": 99}, "OMA-7K3QF-92").ok
    assert not check_hello(good, "").ok
    # Wrong room, or no room at all: the guest list, if you like.
    assert not check_hello({**good, "room": "OMA-AAAAA-11"}, "OMA-7K3QF-92").ok
    assert not check_hello({**good, "room": ""}, "OMA-7K3QF-92").ok
    assert "room code" in check_hello({**good, "room": ""}, "OMA-7K3QF-92").reason


@pytest.mark.skipif(not crypto_available(), reason="needs the cryptography package")
def test_the_nonce_round_trip_proves_the_key_holder(tmp_path):
    from omapoker.net.server import ClientConn

    player = make_player(tmp_path, "Joseph")
    nonce = make_challenge()
    signature = player.profile.identity.sign(nonce)

    class FakeSock:
        def sendall(self, payload):
            return None

        def shutdown(self, how):
            return None

        def close(self):
            return None

    conn = ClientConn(FakeSock(), ("127.0.0.1", 5000), server=None)  # type: ignore[arg-type]
    conn.public_hex = player.profile.identity.public_hex

    assert check_auth(conn, {"t": "auth", "signature": signature}, nonce).ok
    assert not check_auth(conn, {"t": "auth", "signature": signature}, make_challenge()).ok
    assert not check_auth(conn, {"t": "auth", "signature": ""}, nonce).ok

    # The signature is a valid Ed25519 signature of the nonce itself, not of a
    # copy: a different nonce must fail even with the same key.
    from omapoker.data.profiles import verify_signature

    assert verify_signature(conn.public_hex, nonce, signature)


def test_the_challenge_message_carries_the_nonce():
    nonce = make_challenge()
    message = challenge_message(nonce)
    assert message["t"] == "challenge"
    assert bytes.fromhex(message["nonce"]) == nonce


# --------------------------------------------------------------------------
# a real socket on loopback
# --------------------------------------------------------------------------

def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def wait_for(predicate, timeout: float = 5.0, interval: float = 0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return predicate()


def test_a_client_can_join_a_host_on_loopback(tmp_path):
    player = make_player(tmp_path, "Joseph")
    code = new_room_code()
    server = TableServer(code, "Joseph's table", host="127.0.0.1", port=0)
    server.start()
    client = TableClient(player, timeout=3.0)
    try:
        client.connect("127.0.0.1", server.port, code)

        hello = wait_for(lambda: _first(server, "message"))
        assert hello is not None
        conn = hello.conn
        assert conn is not None
        message = hello.message
        assert message["t"] == "hello"
        assert message["name"] == "Joseph"

        # The host runs the handshake by hand, exactly as the interface does:
        # accept the hello, remember the key, then ask for a signature.
        verdict = check_hello(message, code)
        assert verdict.ok
        conn.public_hex = message["public"]
        conn.name = message["name"]
        nonce = make_challenge()
        conn.nonce = nonce
        server.send(conn, challenge_message(nonce))
        auth = wait_for(lambda: _nth(server, "message"))
        assert auth is not None and auth.message["t"] == "auth"
        assert check_auth(auth.conn, auth.message, nonce).ok

        server.send(conn, {"t": "welcome", "seat": 1, "host": "Joseph's table", "code": code})
        announced = wait_for(lambda: _typed(client, "welcome"))
        assert announced is not None
        for message in client.poll():
            client.absorb(message)
        assert client.state.joined
        assert client.state.seat == 1
        assert client.state.room_code == code

        # Actions the other way.
        assert client.action("call", 0)
        action = wait_for(lambda: _action_event(server))
        assert action is not None
        assert action.conn is not None
        assert action.message == {"t": "action", "action": "call", "amount": 0}
    finally:
        client.close()
        server.stop()


def test_a_bad_room_code_reaches_the_host_intact(tmp_path):
    player = make_player(tmp_path, "Joseph")
    server = TableServer(new_room_code(), host="127.0.0.1", port=0)
    server.start()
    client = TableClient(player, timeout=3.0)
    try:
        client.connect("127.0.0.1", server.port, "OMA-AAAAA-11")
        hello = wait_for(lambda: _first(server, "message"))
        assert hello is not None
        # The guest says which room it thinks it is knocking on.
        assert not server.accepts(hello.message["room"])
        assert not check_hello(hello.message, server.room_code).ok
        server.send(hello.conn, {"t": "reject", "reason": "wrong room code"})
        rejected = wait_for(lambda: _typed(client, "reject"))
        assert rejected is not None
        client.absorb(rejected)
        assert "wrong room code" in client.state.error
    finally:
        client.close()
        server.stop()


def test_the_host_notices_a_client_leaving(tmp_path):
    player = make_player(tmp_path, "Joseph")
    server = TableServer(new_room_code(), host="127.0.0.1", port=0)
    server.start()
    client = TableClient(player, timeout=3.0)
    try:
        client.connect("127.0.0.1", server.port, server.room_code)
        assert wait_for(lambda: server.open_connections() == 1)
        client.close()
        assert wait_for(lambda: server.open_connections() == 0)
        left = wait_for(lambda: _kind(server, "left"))
        assert left is not None
    finally:
        client.close()
        server.stop()


def test_the_server_refuses_a_crowd(tmp_path):
    server = TableServer(new_room_code(), host="127.0.0.1", port=0, max_players=1)
    server.start()
    sockets = []
    try:
        first = socket.create_connection(("127.0.0.1", server.port), timeout=3)
        sockets.append(first)
        assert wait_for(lambda: server.open_connections() == 1)
        second = socket.create_connection(("127.0.0.1", server.port), timeout=3)
        sockets.append(second)
        assert wait_for(lambda: b"reject" in second.recv(4096))
        assert server.full is False  # nobody has said hello yet
    finally:
        for sock in sockets:
            sock.close()
        server.stop()


def test_stopping_the_server_says_goodbye(tmp_path):
    player = make_player(tmp_path, "Joseph")
    server = TableServer(new_room_code(), host="127.0.0.1", port=0)
    server.start()
    client = TableClient(player, timeout=3.0)
    client.connect("127.0.0.1", server.port, server.room_code)
    assert wait_for(lambda: server.open_connections() == 1)
    server.stop()
    goodbye = wait_for(lambda: _typed(client, "bye"), timeout=3.0)
    assert goodbye is not None
    client.close()


def test_a_client_with_no_hello_is_eventually_kicked(tmp_path):
    server = TableServer(new_room_code(), host="127.0.0.1", port=0)
    server.start()
    sock = socket.create_connection(("127.0.0.1", server.port), timeout=3)
    try:
        assert wait_for(lambda: server.open_connections() == 1)
        kicked = server.kick_idle(age=0.0)
        assert len(kicked) == 1
        assert server.open_connections() == 0
    finally:
        sock.close()
        server.stop()


def test_a_server_that_never_started_is_harmless():
    server = TableServer("OMA-7K3QF-92")
    assert server.running is False
    assert server.poll() == []
    assert server.port == 0
    server.stop()


def test_the_join_string_uses_the_bound_port():
    server = TableServer("OMA-7K3QF-92", host="127.0.0.1", port=0)
    server.start()
    try:
        text = server.join_string("192.168.1.42")
        assert text == f"omapoker://192.168.1.42:{server.port}/OMA-7K3QF-92"
        assert parse_join_string(text).port == server.port
    finally:
        server.stop()


def test_malformed_input_gets_a_reject_instead_of_a_crash(tmp_path):
    server = TableServer(new_room_code(), host="127.0.0.1", port=0)
    server.start()
    sock = socket.create_connection(("127.0.0.1", server.port), timeout=3)
    try:
        sock.sendall(b"this is not json\n")
        assert socket_wait_for(sock, b"reject")
        # The server is still alive and still accepting.
        assert server.running
        assert server.open_connections() == 1
    finally:
        sock.close()
        server.stop()


# --------------------------------------------------------------------------
# tiny helpers for draining events
# --------------------------------------------------------------------------

def _kind(server, kind):
    for event in server.poll():
        if event.kind == kind:
            return event
    return None


def _first(server, kind):
    return _kind(server, kind)


def _nth(server, kind):
    return _kind(server, kind)


def _action_event(server):
    """The host's view of the next action message."""
    for event in server.poll():
        if event.kind == "message" and (event.message or {}).get("t") == "action":
            return event
    return None


def _typed(client, message_type):
    for message in client.poll():
        client.absorb(message)
        if message.get("t") == message_type:
            return message
    return None


def socket_wait_for(sock, needle: bytes, timeout: float = 3.0) -> bool:
    sock.settimeout(timeout)
    try:
        return needle in sock.recv(4096)
    except OSError:
        return False
    finally:
        sock.settimeout(None)
