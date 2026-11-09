"""The leak report.

Statistics only help if they are phrased for the person reading them. A newcomer
gets plain sentences; an advanced player gets the frequencies and the target
ranges to compare against.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..data.stats import Stats
from .dial import AssistDial

#: Commonly cited healthy ranges for a six-to-nine handed game.
TARGETS = {
    "vpip": (18.0, 30.0),
    "pfr": (14.0, 26.0),
    "aggression": (1.2, 4.0),
    "vpip_pfr_gap": (0.0, 12.0),
    "showdown_win": (45.0, 70.0),
}


@dataclass
class Leak:
    """One finding, with a headline and advice."""

    title: str
    detail: str
    severity: str  # "good" | "minor" | "major"
    numbers: str = ""

    def line(self) -> str:
        return f"{self.title}. {self.detail}"


def _band(value: float, band: tuple[float, float]) -> bool:
    return band[0] <= value <= band[1]


def find_leaks(stats: Stats, dial: AssistDial | None = None, min_hands: int = 20) -> list[Leak]:
    """Analyse a player's numbers and describe what to work on."""
    dial = dial or AssistDial(3)
    verbose = dial.level >= 3

    if stats.hands < min_hands:
        return [
            Leak(
                "Not enough hands yet",
                f"Play at least {min_hands} hands and this report becomes useful. "
                f"You have played {stats.hands}.",
                "minor",
            )
        ]

    leaks: list[Leak] = []

    # ---- VPIP: how many hands you play ---------------------------------
    vpip = stats.vpip * 100
    if vpip >= 55:
        leaks.append(
            Leak(
                "You play far too many hands",
                (
                    "You are in almost every pot. That is the single most expensive "
                    "habit in poker, because it means playing hands that lose money."
                    if not verbose
                    else f"Playing {vpip:.0f}% of hands is roughly double a healthy "
                    f"range. Tighten up from early position first."
                ),
                "major",
                f"VPIP {vpip:.0f}% (target {TARGETS['vpip'][0]:.0f}-{TARGETS['vpip'][1]:.0f}%)",
            )
        )
    elif vpip > TARGETS["vpip"][1]:
        leaks.append(
            Leak(
                "You play a little too many hands",
                "You are in more pots than you should be. Folding more from early "
                "position is the cheapest fix in poker.",
                "minor",
                f"VPIP {vpip:.0f}% (target {TARGETS['vpip'][0]:.0f}-{TARGETS['vpip'][1]:.0f}%)",
            )
        )
    elif _band(vpip, TARGETS["vpip"]) or vpip >= 12:
        leaks.append(
            Leak(
                "Your hand selection is reasonable",
                "You are not giving away money before the flop.",
                "good",
                f"VPIP {vpip:.0f}%",
            )
        )

    # ---- PFR against VPIP ----------------------------------------------
    gap = stats.vpip_pfr_gap * 100
    if gap > 25:
        leaks.append(
            Leak(
                "You call far more than you raise",
                (
                    "You are letting other people decide how big the pot gets. "
                    "Raising wins pots you would otherwise lose."
                    if not verbose
                    else "A large gap between VPIP and PFR means a passive preflop "
                    "game. Raising your playable hands is worth more than calling them."
                ),
                "major",
                f"VPIP {vpip:.0f}% vs PFR {stats.pfr * 100:.0f}% (gap {gap:.0f}%, "
                f"target under {TARGETS['vpip_pfr_gap'][1]:.0f}%)",
            )
        )

    # ---- aggression -----------------------------------------------------
    af = stats.aggression_factor
    if (stats.bets + stats.raises + stats.calls) >= 20:
        if af < 0.8:
            leaks.append(
                Leak(
                    "You are too passive after the flop",
                    (
                        "You call much more often than you bet or raise."
                        if not verbose
                        else "With an aggression factor below 1 you are mostly "
                        "reacting. Betting and raising is how value gets collected."
                    ),
                    "major" if af < 0.5 else "minor",
                    f"Aggression factor {af:.2f} (target "
                    f"{TARGETS['aggression'][0]:.1f}-{TARGETS['aggression'][1]:.1f})",
                )
            )
        elif af > 5:
            leaks.append(
                Leak(
                    "You may be too aggressive",
                    "You bet and raise a great deal. Check that you are not "
                    "bluffing into players who never fold.",
                    "minor",
                    f"Aggression factor {af:.2f}",
                )
            )
        else:
            leaks.append(
                Leak(
                    "Your aggression is well balanced",
                    "You are not a calling station and not a maniac.",
                    "good",
                    f"Aggression factor {af:.2f}",
                )
            )

    # ---- showdowns ------------------------------------------------------
    if stats.showdowns >= 8:
        win = stats.showdown_win_rate * 100
        if win < TARGETS["showdown_win"][0]:
            leaks.append(
                Leak(
                    "You lose too many showdowns",
                    (
                        "You are reaching the end with worse hands than your "
                        "opponents. Try folding earlier when someone keeps betting."
                        if not verbose
                        else "A low showdown win rate usually means calling down too "
                        "lightly. When they bet again on the river, believe them more often."
                    ),
                    "major",
                    f"Won {win:.0f}% of {stats.showdowns} showdowns (target "
                    f"{TARGETS['showdown_win'][0]:.0f}%+)",
                )
            )
        else:
            leaks.append(
                Leak(
                    "You win your fair share at showdown",
                    "When hands are shown, yours hold up.",
                    "good",
                    f"Won {win:.0f}% of {stats.showdowns} showdowns",
                )
            )

    # ---- results --------------------------------------------------------
    if stats.net_chips > 0:
        leaks.append(
            Leak(
                "You are up overall",
                f"Results so far are a win, but remember a small sample proves "
                f"very little.",
                "good",
                f"{stats.net_chips:+,} chips",
            )
        )
    else:
        leaks.append(
            Leak(
                "You are down overall",
                "Losing is the normal state of a learner. Fix the habits above "
                "before worrying about the total.",
                "minor",
                f"{stats.net_chips:+,} chips",
            )
        )

    return leaks


def report_text(stats: Stats, dial: AssistDial | None = None) -> str:
    """The whole report as one block of text."""
    lines = ["Leak report", ""]
    for label, value in stats.summary_lines():
        lines.append(f"  {label:<18} {value}")
    lines.append("")
    for leak in find_leaks(stats, dial):
        marker = {"good": "[ok]", "minor": "[!]", "major": "[!!]"}[leak.severity]
        lines.append(f"{marker} {leak.line()}")
        if leak.numbers and dial and dial.level >= 2:
            lines.append(f"      {leak.numbers}")
    return "\n".join(lines)
