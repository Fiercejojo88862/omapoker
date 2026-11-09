"""The coach: dial gates, ranges, advice and the leak report."""

from __future__ import annotations

import random

import pytest

from omapoker.coach import (
    AssistDial,
    build_advice,
    expand,
    expand_all,
    find_leaks,
    hand_class,
    in_range,
    load_content,
    position_for,
    range_percent,
    report_text,
    review_hand,
)
from omapoker.coach.advice import describe_starting_hand
from omapoker.data.stats import HandRecord, Stats
from omapoker.engine import cards as C
from omapoker.engine.bots import make_bot
from omapoker.engine.game import Action, Hand, Seat, Street


def holes(text: str) -> list[int]:
    return C.parse_cards(text)


def table(stacks=(1000, 1000, 1000), button=0, seed=b"coach"):
    seats = [Seat(i, f"P{i}", stacks[i]) for i in range(len(stacks))]
    hand = Hand(seats, button, 25, 50, seed=seed)
    hand.start()
    return hand


# --------------------------------------------------------------------------
# the dial
# --------------------------------------------------------------------------

def test_dial_levels_gate_features_cumulatively():
    assert not AssistDial(0).enabled
    assert not AssistDial(0).shows_hand_name

    one = AssistDial(1)
    assert one.shows_hand_name and not one.shows_numbers

    two = AssistDial(2)
    assert two.shows_hand_name and two.shows_numbers and not two.shows_advice

    three = AssistDial(3)
    assert three.shows_advice and not three.shows_charts

    four = AssistDial(4)
    assert four.shows_charts and not four.shows_walkthrough

    five = AssistDial(5)
    assert five.shows_walkthrough and five.shows_review


def test_dial_rejects_out_of_range_values():
    with pytest.raises(ValueError):
        AssistDial(6)
    with pytest.raises(ValueError):
        AssistDial(-1)


def test_dial_bumps_and_reports():
    dial = AssistDial(3)
    assert dial.bumped(1).level == 4
    assert dial.bumped(-5).level == 0
    assert dial.bumped(9).level == 5
    assert "3/5" in dial.describe()
    assert len(dial.explain()) == 6


# --------------------------------------------------------------------------
# ranges
# --------------------------------------------------------------------------

def test_hand_class_notation():
    assert hand_class(holes("As Ah")) == "AA"
    assert hand_class(holes("As Ks")) == "AKs"
    assert hand_class(holes("As Kd")) == "AKo"
    assert hand_class(holes("2d 7c")) == "72o"
    assert hand_class(holes("Td 9d")) == "T9s"


def test_expanding_pairs_and_plus_notation():
    assert expand("QQ") == {"QQ"}
    assert expand("TT+") == {"TT", "JJ", "QQ", "KK", "AA"}
    assert expand("22-44") == {"22", "33", "44"}
    assert expand("55+") == {"55", "66", "77", "88", "99", "TT", "JJ", "QQ", "KK", "AA"}


def test_expanding_suited_and_offsuit_plus_notation():
    assert expand("A9s+") == {"A9s", "ATs", "AJs", "AQs", "AKs"}
    assert expand("KTo+") == {"KTo", "KJo", "KQo"}
    assert expand("T9s") == {"T9s"}
    assert expand("") == set()


def test_in_range_uses_the_notation():
    assert in_range(holes("As Ks"), ["A9s+"])
    assert not in_range(holes("As Ks"), ["77+", "QJs"])
    assert in_range(holes("9h 9s"), ["55+"])
    assert not in_range(holes("4h 4s"), ["55+"])


def test_range_percent_is_plausible():
    tight = range_percent(["QQ+", "AKs", "AKo"])
    wide = range_percent(["22+", "A2s+", "K7s+", "Q8s+", "A8o+", "K9o+"])
    assert 0 < tight < wide < 100
    assert tight < 5


def test_position_buckets():
    # Nine seats, button at seat 0.
    assert position_for(0, 0, 9) == "late"
    assert position_for(1, 0, 9) == "blinds"   # small blind
    assert position_for(2, 0, 9) == "blinds"   # big blind
    assert position_for(3, 0, 9) == "early"    # under the gun
    assert position_for(8, 0, 9) == "late"     # cutoff


# --------------------------------------------------------------------------
# advice
# --------------------------------------------------------------------------

def test_dial_zero_says_nothing_at_all():
    hand = table()
    advice = build_advice(hand, hand.to_act, AssistDial(0))
    assert advice.is_empty()


def test_level_one_only_names_the_hand():
    hand = table()
    advice = build_advice(hand, hand.to_act, AssistDial(1))
    assert advice.hand_name
    assert not advice.numbers
    assert not advice.headline


def test_level_two_adds_equity_and_pot_odds():
    hand = table()
    advice = build_advice(hand, hand.to_act, AssistDial(2), rng=random.Random(1))
    assert advice.numbers
    assert advice.equity is not None
    assert any("Equity" in line for line in advice.numbers)
    assert any("Pot odds" in line for line in advice.numbers)
    assert not advice.headline


def test_level_three_recommends_an_action_with_a_reason():
    hand = table()
    advice = build_advice(hand, hand.to_act, AssistDial(3), rng=random.Random(2))
    assert advice.action in {"Fold", "Call", "Raise", "Bet", "Check"}
    assert advice.headline and advice.reason
    assert not advice.chart


def test_level_four_adds_charts_and_position():
    hand = table()
    advice = build_advice(hand, hand.to_act, AssistDial(4), rng=random.Random(3))
    assert advice.chart
    assert any("range" in line for line in advice.chart)
    assert not advice.walkthrough


def test_level_five_walks_through_the_decision():
    hand = table()
    advice = build_advice(hand, hand.to_act, AssistDial(5), rng=random.Random(4))
    assert advice.walkthrough
    assert len(advice.walkthrough) >= 5
    assert advice.walkthrough[0].startswith("1.")


def test_advice_names_a_premium_hand_as_a_favourite_to_raise():
    hand = table(seed=b"aces")
    # Force pocket aces into seat 0, which is first to act.
    aces = holes("As Ah")
    rigged = [0] * 52
    deck = list(range(52))
    # Deal order for button 0 with three players: seats 1, 2, 0 twice.
    rest = [c for c in deck if c not in aces]
    rigged = [rest.pop(0), rest.pop(0), aces[0], rest.pop(0), rest.pop(0), aces[1]] + rest
    seats = [Seat(i, f"P{i}", 1000) for i in range(3)]
    hand = Hand(seats, 0, 25, 50, deck=rigged)
    hand.start()
    assert sorted(hand.seats[0].hole) == sorted(aces)
    advice = build_advice(hand, 0, AssistDial(3), rng=random.Random(5))
    assert advice.equity and advice.equity > 0.7
    assert advice.action in {"Raise", "Call"}


def test_advice_describes_a_postflop_hand_by_name():
    hand = table()
    hand.act(Action.CALL, 50)
    hand.act(Action.CALL, 50)
    hand.act(Action.CHECK)
    assert hand.street is Street.FLOP
    advice = build_advice(hand, hand.to_act, AssistDial(3), rng=random.Random(6))
    assert advice.hand_name
    assert advice.action


def test_starting_hand_descriptions_read_naturally():
    assert "pocket pair of aces" in describe_starting_hand(holes("As Ah"))
    assert "suited" in describe_starting_hand(holes("As Ks"))
    assert "offsuit" in describe_starting_hand(holes("As Kd"))


def test_review_appears_only_at_the_top_level():
    hand = table()
    while hand.street is not Street.COMPLETE:
        legal = hand.legal_actions()
        hand.act(
            Action.CHECK if legal.can_check else Action.CALL,
            legal.call_amount if legal.can_call else 0,
        )
    from omapoker.session import HandOutcome

    outcome = HandOutcome(
        hand_id=1,
        board=list(hand.board),
        reveal=dict(hand.result.revealed),
        totals=dict(hand.result.totals),
        net={0: 50},
        showdown=hand.result.showdown,
        winning_score=hand.result.winning_score,
    )
    assert review_hand(outcome, hand.seats, 0, AssistDial(4)) == []
    lines = review_hand(outcome, hand.seats, 0, AssistDial(5))
    assert lines
    assert any("Board" in line for line in lines)


# --------------------------------------------------------------------------
# leak report
# --------------------------------------------------------------------------

def make_stats(**overrides) -> Stats:
    stats = Stats(profile_id="t")
    stats.hands = overrides.get("hands", 100)
    stats.hands_won = overrides.get("hands_won", 30)
    stats.showdowns = overrides.get("showdowns", 20)
    stats.showdowns_won = overrides.get("showdowns_won", 6)
    stats.vpip_hands = overrides.get("vpip_hands", 60)
    stats.pfr_hands = overrides.get("pfr_hands", 10)
    stats.bets = overrides.get("bets", 5)
    stats.raises = overrides.get("raises", 5)
    stats.calls = overrides.get("calls", 60)
    stats.net_chips = overrides.get("net_chips", -500)
    return stats


def test_leak_report_asks_for_more_hands_when_the_sample_is_tiny():
    stats = Stats(profile_id="t")
    stats.hands = 5
    leaks = find_leaks(stats)
    assert len(leaks) == 1
    assert "Not enough hands" in leaks[0].title


def test_leak_report_flags_loose_passive_play():
    stats = make_stats()
    leaks = find_leaks(stats, AssistDial(3))
    titles = " ".join(leak.title for leak in leaks)
    assert "too many hands" in titles
    assert "call far more than you raise" in titles
    assert "too passive" in titles
    severities = {leak.severity for leak in leaks}
    assert "major" in severities


def test_leak_report_praises_a_tight_aggressive_player():
    stats = make_stats(
        vpip_hands=22,
        pfr_hands=18,
        bets=40,
        raises=30,
        calls=30,
        showdowns=10,
        showdowns_won=6,
        net_chips=5000,
    )
    leaks = find_leaks(stats, AssistDial(3))
    goods = [leak for leak in leaks if leak.severity == "good"]
    assert len(goods) >= 3
    assert any("up overall" in leak.title for leak in leaks)


def test_report_text_includes_numbers_only_when_the_dial_allows():
    stats = make_stats()
    quiet = report_text(stats, AssistDial(1))
    chatty = report_text(stats, AssistDial(3))
    assert quiet.count("\n") < chatty.count("\n")
    assert "Hands played" in chatty
    assert "VPIP" in chatty


# --------------------------------------------------------------------------
# content
# --------------------------------------------------------------------------

def test_bundled_content_loads():
    content = load_content()
    assert len(content.glossary) > 10
    assert len(content.lessons) >= 5
    assert content.open_ranges["early"]
    assert content.three_bet_ranges["late"]


def test_glossary_search():
    content = load_content()
    assert content.search("") == sorted(content.glossary.items())
    hits = content.search("equity")
    assert hits
    assert any("Equity" in name for name, _ in hits)


def test_lessons_are_filtered_by_experience():
    content = load_content()
    newcomer = content.lessons_for("newcomer")
    advanced = content.lessons_for("advanced")
    assert 0 < len(newcomer) <= len(advanced)
    assert all(l["level"] in ("newcomer",) for l in newcomer)
