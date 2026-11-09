"""Persistence tests: atomic writes, backups, bankrolls, identity, stats."""

from __future__ import annotations

import json
import os
import stat

import pytest

from omapoker.data import (
    DataStore,
    Stats,
    analyse_hand,
    code_for_public_key,
    code_matches_key,
    crypto_available,
    level_for,
    ladder,
    blinds_for,
    normalise_code,
    verify_signature,
)
from omapoker.data.bankroll import STARTING_CHIPS, Bankroll
from omapoker.data.profiles import EXPERIENCE_ASSIST, Identity, Profile
from omapoker.data.store import IdentifiedStore, JsonStore, read_json
from omapoker.engine.bots import make_bot
from omapoker.engine.game import Action, Hand, Seat, Street


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------

def test_atomic_write_and_read(tmp_path):
    store = JsonStore(tmp_path)
    store.save("thing", {"a": 1})
    assert store.load("thing") == {"a": 1}
    assert store.exists("thing")


def test_saving_takes_a_backup_and_restores(tmp_path):
    store = JsonStore(tmp_path)
    store.save("thing", {"version": 1})
    store.save("thing", {"version": 2})
    store.save("thing", {"version": 3})
    backups = store.backups_for("thing")
    assert len(backups) == 2
    assert store.load("thing")["version"] == 3
    # Backups are ordered most recent first.
    assert read_json(backups[0])["version"] == 2
    assert read_json(backups[1])["version"] == 1
    store.restore("thing", backups[1])
    assert store.load("thing")["version"] == 1


def test_backups_are_pruned(tmp_path):
    store = JsonStore(tmp_path, keep=3)
    for version in range(10):
        store.save("thing", {"version": version})
    assert len(store.backups_for("thing")) == 3


def test_profile_files_are_private(tmp_path):
    store = JsonStore(tmp_path)
    store.save("secret", {"key": "value"})
    mode = stat.S_IMODE(os.stat(store.path("secret")).st_mode)
    assert mode == 0o600, oct(mode)


def test_corrupt_json_does_not_explode(tmp_path):
    store = JsonStore(tmp_path)
    store.path("broken").write_text("{ not json", encoding="utf-8")
    assert store.load("broken", default={"safe": True}) == {"safe": True}


def test_delete_keeps_a_backup_by_default(tmp_path):
    store = JsonStore(tmp_path)
    store.save("thing", {"a": 1})
    assert store.delete("thing")
    assert not store.exists("thing")
    assert store.backups_for("thing")


def test_identified_store_makes_unique_slugs(tmp_path):
    store = IdentifiedStore(tmp_path)
    assert store.unique_slug("Ada Lovelace") == "ada-lovelace"
    store.save("ada-lovelace", {})
    assert store.unique_slug("Ada Lovelace") == "ada-lovelace-2"
    assert store.unique_slug("!!!") == "profile"


# --------------------------------------------------------------------------
# ladder
# --------------------------------------------------------------------------

def test_ladder_shape_matches_the_brief():
    levels = ladder()
    assert levels[0].buy_in == 5_000
    assert levels[-1].buy_in == 100_000
    assert len(levels) == 96
    steps = {b.buy_in - a.buy_in for a, b in zip(levels, levels[1:])}
    assert steps == {1_000}, "buy-ins must climb 1,000 at a time"


def test_blinds_scale_with_the_buy_in():
    assert (level_for(5_000).small_blind, level_for(5_000).big_blind) == (25, 50)
    assert (level_for(10_000).small_blind, level_for(10_000).big_blind) == (50, 100)
    assert (level_for(100_000).small_blind, level_for(100_000).big_blind) == (500, 1_000)
    assert blinds_for(6_000) == (30, 60)
    assert blinds_for(7_000) == (35, 70)


def test_ladder_rejects_below_the_minimum():
    with pytest.raises(ValueError):
        level_for(4_000)


# --------------------------------------------------------------------------
# bankroll
# --------------------------------------------------------------------------

def test_a_new_profile_starts_with_100k():
    bankroll = Bankroll(profile_id="tester")
    assert bankroll.chips == 100_000 == STARTING_CHIPS
    assert bankroll.can_buy_in()


def test_buy_in_and_cash_out_move_chips_and_write_a_ledger():
    bankroll = Bankroll(profile_id="tester")
    taken = bankroll.buy_in(5_000, "Micro 25/50")
    assert taken == 5_000
    assert bankroll.chips == 95_000
    bankroll.cash_out(7_500, "left the table up")
    assert bankroll.chips == 102_500
    assert [e.kind for e in bankroll.ledger] == ["buy_in", "cash_out"]
    assert bankroll.ledger[-1].balance == 102_500


def test_buy_in_cannot_exceed_the_bankroll():
    bankroll = Bankroll(profile_id="tester", chips=3_000)
    assert bankroll.buy_in(5_000) == 3_000
    assert bankroll.chips == 0
    assert not bankroll.can_buy_in()


def test_settle_and_reset():
    bankroll = Bankroll(profile_id="tester")
    bankroll.settle(-12_345, "session")
    assert bankroll.chips == 87_655
    bankroll.reset()
    assert bankroll.chips == STARTING_CHIPS
    assert bankroll.ledger[-1].kind == "reset"


def test_settle_never_goes_negative():
    bankroll = Bankroll(profile_id="tester", chips=100)
    bankroll.settle(-500)
    assert bankroll.chips == 0


def test_bankroll_round_trips_through_disk(tmp_path):
    store = DataStore(tmp_path)
    profile = store.create_profile("Ada", "skilled")
    bankroll = store.bankroll_for(profile.id)
    assert bankroll.chips == STARTING_CHIPS
    bankroll.buy_in(5_000)
    bankroll.cash_out(6_000)
    store.bankroll_store.save_bankroll(bankroll)

    reloaded = store.bankroll_for(profile.id)
    assert reloaded.chips == 101_000
    assert len(reloaded.ledger) >= 2


# --------------------------------------------------------------------------
# identity
# --------------------------------------------------------------------------

def test_identity_codes_are_stable_and_typed_friendly():
    identity = Identity.generate()
    code = identity.code
    assert code.startswith("OMA-ID-")
    assert len(code) == len("OMA-ID-XXXXXXXX-XXXXXXXX")
    assert identity.code == code
    # Lowercase, spaced and hyphen-free forms all normalise back.
    assert normalise_code(code.lower()) == code
    assert normalise_code(code.replace("-", " ")) == code
    assert normalise_code(code.replace("-", "")) == code


def test_a_code_is_bound_to_its_key():
    first = Identity.generate()
    second = Identity.generate()
    assert code_matches_key(first.code, first.public_hex)
    assert not code_matches_key(first.code, second.public_hex)
    assert first.code != second.code


@pytest.mark.skipif(not crypto_available(), reason="needs the cryptography package")
def test_signature_verification():
    identity = Identity.generate()
    assert identity.verified
    nonce = os.urandom(16)
    signature = identity.sign(nonce)
    assert signature
    assert verify_signature(identity.public_hex, nonce, signature)
    # A different nonce must not verify, and neither must a tampered signature.
    assert not verify_signature(identity.public_hex, os.urandom(16), signature)
    assert not verify_signature(identity.public_hex, nonce, "00" * 64)
    assert not verify_signature("zz", nonce, signature)


@pytest.mark.skipif(not crypto_available(), reason="needs the cryptography package")
def test_two_profiles_cannot_impersonate_each_other():
    alice = Identity.generate()
    mallory = Identity.generate()
    nonce = os.urandom(16)
    assert not verify_signature(alice.public_hex, nonce, mallory.sign(nonce))


# --------------------------------------------------------------------------
# profiles
# --------------------------------------------------------------------------

def test_profile_experience_presets_the_assist_dial():
    assert Profile.new("a", "A", "newcomer").assist_level == 5
    assert Profile.new("b", "B", "casual").assist_level == EXPERIENCE_ASSIST["casual"]
    assert Profile.new("c", "C", "advanced").assist_level == 1


def test_create_load_and_delete_a_profile(tmp_path):
    store = DataStore(tmp_path)
    profile = store.create_profile("Alice", "casual")
    assert profile.id == "alice"
    assert store.active_profile_id() == "alice"

    listed = store.list_profiles()
    assert [p.name for p in listed] == ["Alice"]

    profile.assist_level = 2
    store.save_profile(profile)
    assert store.load_profile("alice").assist_level == 2

    assert store.delete_profile("alice")
    assert store.list_profiles() == []
    assert store.active_profile_id() is None


def test_profile_identity_survives_a_round_trip(tmp_path):
    store = DataStore(tmp_path)
    created = store.create_profile("Ada", "advanced")
    code = created.identity.code
    loaded = store.load_profile("ada")
    assert loaded is not None
    assert loaded.identity.code == code
    assert loaded.identity.public_hex == created.identity.public_hex


def test_settings_defaults_and_overrides(tmp_path):
    store = DataStore(tmp_path)
    settings = store.settings()
    assert settings["cards_style"] == "glyphs"
    assert settings["lan_port"] == 8760
    store.update_setting("cards_style", "ascii")
    assert store.settings()["cards_style"] == "ascii"
    # Unknown keys are preserved rather than dropped.
    store.save_settings({"custom": 1})
    assert store.settings()["custom"] == 1


def test_player_bundle_ties_everything_together(tmp_path):
    store = DataStore(tmp_path)
    profile = store.create_profile("Jo", "casual")
    player = store.player(profile.id)
    assert player is not None
    assert player.chips == STARTING_CHIPS
    player.bankroll.buy_in(5_000)
    player.stats.hands = 3
    store.save_player(player)
    again = store.player(profile.id)
    assert again.chips == 95_000
    assert again.stats.hands == 3


# --------------------------------------------------------------------------
# stats
# --------------------------------------------------------------------------

def test_analyse_hand_detects_vpip_pfr_and_net():
    seats = [
        Seat(0, "Hero", 1000),
        make_bot(1, "Ada", 1000, "medium"),
        make_bot(2, "Boris", 1000, "medium"),
    ]
    hand = Hand(seats, button=0, small_blind=25, big_blind=50, seed=b"stats")
    hand.start()
    # Hero (the button) raises preflop, then everyone folds.
    hand.act(Action.RAISE, 150)
    hand.act(Action.FOLD)
    hand.act(Action.FOLD)

    record, actions = analyse_hand(hand, 0, table="Micro 25/50")
    assert record.vpip is True
    assert record.pfr is True
    assert record.won is True
    assert record.net > 0
    assert "raise" in actions
    assert record.bot_level == ""


def test_stats_rates_are_computed_correctly():
    stats = Stats(profile_id="t")
    from omapoker.data.stats import HandRecord

    for index in range(10):
        stats.record(
            HandRecord(
                hand_id=index,
                won=index % 2 == 0,
                showdown=index % 5 == 0,
                saw_flop=index % 2 == 1,
                vpip=index < 4,
                pfr=index < 2,
                net=100 if index % 2 == 0 else -100,
            ),
            actions=["bet", "bet", "call"] if index % 3 == 0 else ["call"],
        )
    assert stats.hands == 10
    assert stats.vpip == pytest.approx(0.4)
    assert stats.pfr == pytest.approx(0.2)
    assert stats.win_rate == pytest.approx(0.5)
    assert stats.vpip_pfr_gap == pytest.approx(0.2)
    assert stats.aggression_factor > 0
    assert stats.net_chips == 0  # five wins, five losses of 100


def test_stats_round_trip(tmp_path):
    store = DataStore(tmp_path)
    profile = store.create_profile("Ada", "casual")
    stats = store.stats_for(profile.id)
    stats.hands = 42
    stats.net_chips = -1234
    store.stats_store.save_stats(stats)
    assert store.stats_for(profile.id).hands == 42
    assert store.stats_for(profile.id).net_chips == -1234
