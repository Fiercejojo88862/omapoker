"""Lessons, glossary and charts, loaded from editable JSON.

Content lives in ``content/`` next to the package (or in a source checkout) so a
player can add their own notes without touching code. If a file is missing or
malformed the built-in defaults are used instead, and the app keeps working.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .. import paths
from .ranges import DEFAULT_OPEN_RANGES, DEFAULT_THREE_BET_RANGES

BUILTIN_GLOSSARY: dict[str, str] = {
    "Position": "Where you sit relative to the button. Acting last is a real edge: you see what everyone does before you decide.",
    "VPIP": "Voluntarily Put money In Pot. How often you play a hand before the flop. Lower is tighter.",
    "PFR": "Preflop Raise. How often you raise before the flop. It should be close to your VPIP if you play aggressively.",
    "Pot odds": "The price you are being offered. Calling 25 into a pot of 100 gives you 4:1, so you need 20% equity to break even.",
    "Equity": "Your share of the pot if the hand were played out many times.",
    "Outs": "Cards left in the deck that improve you to a strong hand. A flush draw has nine.",
    "The 2 and 4 rule": "Multiply your outs by 4 on the flop and 2 on the turn to estimate your chance of improving.",
    "Range": "The set of hands a player could hold, rather than one exact hand. Thinking in ranges is the biggest leap a learner makes.",
    "Tilt": "Playing badly because you are upset about a previous hand. The most expensive emotion in poker.",
    "All in": "Betting every chip you have.",
    "The button": "The seat that acts last after the flop. It moves one seat clockwise each hand.",
    "Blinds": "Forced bets posted before the cards are dealt, to make sure there is something to play for.",
    "Cooler": "A huge hand beaten by a bigger one. Nobody escapes these, so do not change how you play after one.",
    "Check": "Declining to bet while staying in the hand.",
    "Raise": "Increasing the current bet.",
    "Call": "Matching the current bet.",
    "Fold": "Giving up the hand and any money already in the pot.",
    "Equity realisation": "The fact that some hands win less often than their raw equity suggests, because they are hard to play. Small suited hands are the classic example.",
    "Bankroll management": "Choosing tables you can afford. If a buy-in is more than a small share of your chips, move down.",
}

BUILTIN_LESSONS: list[dict] = [
    {
        "id": "rules",
        "title": "How a hand works",
        "level": "newcomer",
        "body": (
            "Every player gets two cards face down. There is a round of betting, "
            "then three shared cards (the flop), another betting round, a fourth "
            "card (the turn), a third round, a fifth card (the river), and a final "
            "round. You make the best five-card hand from your two cards and the "
            "five shared cards, or from the shared cards alone. The last player to "
            "act after the flop sits on the button, and that advantage is real."
        ),
    },
    {
        "id": "rankings",
        "title": "Hand rankings, best to worst",
        "level": "newcomer",
        "body": (
            "Straight flush, four of a kind, full house, flush, straight, three of "
            "a kind, two pair, one pair, high card. A straight is five cards in a "
            "row; an ace can be high or low, so A-2-3-4-5 counts, and it is the "
            "weakest straight. A flush is five cards of one suit, and the highest "
            "card decides between two flushes."
        ),
    },
    {
        "id": "why-fold",
        "title": "Most hands should be folded",
        "level": "casual",
        "body": (
            "You are dealt two cards from 52. Most of those combinations lose money "
            "if you play them. Strong players fold the majority of hands before the "
            "flop. Fold more than feels natural, especially in early position."
        ),
    },
    {
        "id": "position",
        "title": "Position is worth more than cards",
        "level": "casual",
        "body": (
            "Acting last means you get to see what everyone does before you commit "
            "chips. That is why the same hand can be a raise on the button and a "
            "fold under the gun. Play more hands late, fewer early."
        ),
    },
    {
        "id": "pot-odds",
        "title": "The price you are being offered",
        "level": "casual",
        "body": (
            "If the pot is 100 and it costs you 25 to call, you are being offered "
            "4:1. You need to win more than 20% of the time for that to be worth "
            "it. Compare that number to your estimate of winning (the 2 and 4 rule "
            "is good enough at the table)."
        ),
    },
    {
        "id": "ranges",
        "title": "Think in ranges, not hands",
        "level": "skilled",
        "body": (
            "You never know one exact hand; you know a set of hands that makes "
            "sense for what they did. When someone raises under the gun, their "
            "range is tight. When they raise on the button, it is wide. Your "
            "decision should be against that whole set."
        ),
    },
    {
        "id": "aggression",
        "title": "Betting beats calling",
        "level": "skilled",
        "body": (
            "Calling only wins when your hand holds up. Betting wins two ways: "
            "your hand holds up, or they fold. That is why a healthy player bets "
            "and raises more often than they call, and why the aggression factor "
            "in your leak report matters."
        ),
    },
    {
        "id": "bankroll",
        "title": "Choose the right table",
        "level": "skilled",
        "body": (
            "A buy-in that is a large share of your chips turns a normal downswing "
            "into a disaster. Move down a level when your bankroll drops, and up "
            "when it grows. The ladder in OmaPoker is built for exactly this."
        ),
    },
    {
        "id": "variance",
        "title": "Losing streaks are normal",
        "level": "advanced",
        "body": (
            "Even a strong player loses sessions and whole weeks. Judge yourself "
            "on the quality of your decisions, not on the result of one hand. The "
            "leak report is a far better mirror than your chip total."
        ),
    },
]


@dataclass
class Content:
    """Loaded coach content."""

    glossary: dict[str, str] = field(default_factory=dict)
    lessons: list[dict] = field(default_factory=list)
    open_ranges: dict[str, list[str]] = field(default_factory=dict)
    three_bet_ranges: dict[str, list[str]] = field(default_factory=dict)
    source: str = "built-in"

    # -- lookups -------------------------------------------------------
    def lesson(self, lesson_id: str) -> dict | None:
        for entry in self.lessons:
            if entry.get("id") == lesson_id:
                return entry
        return None

    def search(self, term: str) -> list[tuple[str, str]]:
        """Search glossary entries by keyword."""
        term = term.strip().lower()
        if not term:
            return sorted(self.glossary.items())
        hits = [
            (name, text)
            for name, text in self.glossary.items()
            if term in name.lower() or term in text.lower()
        ]
        return sorted(hits)

    def lessons_for(self, level: str) -> list[dict]:
        order = ["newcomer", "casual", "skilled", "advanced"]
        if level not in order:
            return list(self.lessons)
        ceiling = order.index(level)
        return [l for l in self.lessons if order.index(l.get("level", "casual")) <= ceiling]


def _load_json(path: Path):
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


@lru_cache(maxsize=4)
def _load(root: str | None = None) -> Content:
    directory = Path(root) if root else paths.content_dir()
    content = Content()
    content.glossary = dict(BUILTIN_GLOSSARY)
    content.lessons = list(BUILTIN_LESSONS)
    content.open_ranges = {k: list(v) for k, v in DEFAULT_OPEN_RANGES.items()}
    content.three_bet_ranges = {k: list(v) for k, v in DEFAULT_THREE_BET_RANGES.items()}

    if directory.is_dir():
        glossary = _load_json(directory / "glossary.json")
        if isinstance(glossary, dict) and glossary:
            content.glossary.update({str(k): str(v) for k, v in glossary.items()})
        lessons = _load_json(directory / "lessons.json")
        if isinstance(lessons, list) and lessons:
            content.lessons = lessons
        charts = _load_json(directory / "charts.json")
        if isinstance(charts, dict):
            if isinstance(charts.get("open_raises"), dict):
                content.open_ranges = {
                    str(k): list(v) for k, v in charts["open_raises"].items()
                }
            if isinstance(charts.get("three_bets"), dict):
                content.three_bet_ranges = {
                    str(k): list(v) for k, v in charts["three_bets"].items()
                }
        content.source = str(directory)

    return content


def load_content(root: str | None = None) -> Content:
    return _load(root)


def reload_content() -> None:
    _load.cache_clear()
