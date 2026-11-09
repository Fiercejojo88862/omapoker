"""Teaching: the assist dial, advice, ranges and the leak report."""

from .advice import Advice, build_advice, review_hand
from .content import Content, load_content, reload_content
from .dial import (
    DEFAULT_LEVEL,
    LEVEL_SUMMARY,
    LEVEL_TITLES,
    MAX_LEVEL,
    MIN_LEVEL,
    AssistDial,
)
from .leaks import Leak, find_leaks, report_text
from .ranges import (
    DEFAULT_OPEN_RANGES,
    DEFAULT_THREE_BET_RANGES,
    describe_range,
    expand,
    expand_all,
    hand_class,
    in_range,
    position_for,
    range_percent,
)

__all__ = [
    "Advice",
    "AssistDial",
    "Content",
    "DEFAULT_LEVEL",
    "DEFAULT_OPEN_RANGES",
    "DEFAULT_THREE_BET_RANGES",
    "LEVEL_SUMMARY",
    "LEVEL_TITLES",
    "Leak",
    "MAX_LEVEL",
    "MIN_LEVEL",
    "build_advice",
    "describe_range",
    "expand",
    "expand_all",
    "find_leaks",
    "hand_class",
    "in_range",
    "load_content",
    "position_for",
    "range_percent",
    "reload_content",
    "report_text",
    "review_hand",
]
