"""The assist dial.

One number, 0 to 5, decided by the player, changeable mid-hand. Each level is a
strict superset of the one below it, so the dial never takes something away
unexpectedly.
"""

from __future__ import annotations

from dataclasses import dataclass

MIN_LEVEL = 0
MAX_LEVEL = 5
DEFAULT_LEVEL = 3

#: What each level adds. Used by the UI and by the settings screen.
LEVEL_SUMMARY = {
    0: "Pure poker. No help at all.",
    1: "Tells you what you are holding.",
    2: "Adds pot odds, your outs and your equity.",
    3: "Adds a recommended action and the reason for it.",
    4: "Adds preflop charts and position advice.",
    5: "Adds a step-by-step walkthrough and a full post-hand review.",
}

LEVEL_TITLES = {
    0: "Off",
    1: "Names only",
    2: "The numbers",
    3: "Tell me what to do",
    4: "Teach me the ranges",
    5: "Walk me through it",
}


@dataclass(frozen=True)
class AssistDial:
    """What the coach is allowed to show at a given level."""

    level: int = DEFAULT_LEVEL

    def __post_init__(self) -> None:
        if not MIN_LEVEL <= self.level <= MAX_LEVEL:
            raise ValueError(f"assist level must be {MIN_LEVEL}-{MAX_LEVEL}")

    # -- feature gates -------------------------------------------------
    @property
    def shows_hand_name(self) -> bool:
        return self.level >= 1

    @property
    def shows_numbers(self) -> bool:
        return self.level >= 2

    @property
    def shows_advice(self) -> bool:
        return self.level >= 3

    @property
    def shows_charts(self) -> bool:
        return self.level >= 4

    @property
    def shows_walkthrough(self) -> bool:
        return self.level >= 5

    @property
    def shows_review(self) -> bool:
        return self.level >= 5

    @property
    def enabled(self) -> bool:
        return self.level > 0

    @property
    def title(self) -> str:
        return LEVEL_TITLES[self.level]

    @property
    def summary(self) -> str:
        return LEVEL_SUMMARY[self.level]

    # -- dialling ------------------------------------------------------
    def bumped(self, delta: int) -> "AssistDial":
        level = max(MIN_LEVEL, min(MAX_LEVEL, self.level + delta))
        return AssistDial(level)

    def with_level(self, level: int) -> "AssistDial":
        return AssistDial(level)

    def describe(self) -> str:
        bar = "".join("\u25cf" if i <= self.level else "\u25cb" for i in range(MAX_LEVEL + 1))
        return f"Assist {self.level}/{MAX_LEVEL} {bar}  {self.title}"

    def explain(self) -> list[tuple[int, str, bool]]:
        """Rows for the settings screen: (level, summary, is_current)."""
        return [
            (level, LEVEL_SUMMARY[level], level == self.level)
            for level in range(MIN_LEVEL, MAX_LEVEL + 1)
        ]
