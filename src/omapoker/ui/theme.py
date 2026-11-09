"""Colours and card rendering.

The palette follows Omarchy's own accent green (``#9ece6a``, the same value in
``/etc/os-release``) against a dark, low-contrast background so a long session is
easy on the eyes.
"""

from __future__ import annotations

from typing import Sequence

from ..engine import cards as C

# --- palette ---------------------------------------------------------------
BACKGROUND = "#16161e"
PANEL = "#1f2335"
PANEL_ALT = "#24283b"
BORDER = "#3b4261"
BORDER_ACTIVE = "#9ece6a"
TEXT = "#c0caf5"
TEXT_DIM = "#565f89"
ACCENT = "#9ece6a"
ACCENT_DIM = "#6a9350"
RED = "#f7768e"
WARN = "#e0af68"
BLUE = "#7aa2f7"
PURPLE = "#bb9af7"
CYAN = "#7dcfff"

# --- the room and the table ------------------------------------------------
#: A padded rail around the felt, and the felt itself. These are deliberately
#: warmer than the UI palette so the table reads as furniture, not a dialog.
RAIL = "#7a5c3e"
RAIL_DARK = "#33241a"
FELT = "#173b2a"
FELT_EDGE = "#4f7a3d"
GOLD = "#e0c07a"
#: Room floor: near-black carpet with a lattice too dim to compete with the felt.
FLOOR = "#0e0e16"
#: Dot shades from the edge of the room inwards, so the table sits in a pool of light.
FLOOR_SHADES = ("#141725", "#191e2d", "#20263a", "#293149")

#: Suit presentation, indexed by suit number (0=c, 1=d, 2=h, 3=s).
SUIT_COLOURS = (CYAN, RED, RED, CYAN)


def suit_colour(card: int) -> str:
    return SUIT_COLOURS[C.suit_of(card)]


def cards_style(preference: str = "glyphs") -> str:
    return "glyphs" if preference != "ascii" else "ascii"


def suit_text(card: int, style: str = "glyphs") -> str:
    if style == "ascii":
        return C.SUIT_SYMBOLS[C.suit_of(card)]
    return C.SUIT_GLYPHS[C.suit_of(card)]


def rank_text(card: int, style: str = "glyphs") -> str:
    return C.RANK_SYMBOLS[C.rank_of(card)]


def card_markup(card: int, style: str = "glyphs") -> str:
    """One card as coloured Rich markup, e.g. ``As`` in red."""
    label = f"{rank_text(card, style)}{suit_text(card, style)}"
    return f"[{suit_colour(card)}]{label}[/]"


def cards_markup(cards: Sequence[int], style: str = "glyphs", sep: str = " ") -> str:
    return sep.join(card_markup(card, style) for card in cards)


HIDDEN_HOLE = "\u2592\u2592"  # two shaded blocks


def hidden_markup(style: str = "glyphs") -> str:
    return f"[{BORDER}]{HIDDEN_HOLE}[/]"


def hole_markup(hole: Sequence[int], style: str = "glyphs", hidden: bool = False) -> str:
    """A player's two cards, or a face-down pair."""
    if hidden or not hole:
        return f"{hidden_markup(style)} {hidden_markup(style)}"
    return cards_markup(hole, style)


# --- card boxes for the board ---------------------------------------------

BOX_WIDTH = 4


def _label(card: int, style: str) -> str:
    return f"{rank_text(card, style)}{suit_text(card, style)}"


def card_box(card: int, style: str = "glyphs", plain: bool = False) -> list[str]:
    """A three-line box for a card, coloured unless ``plain``."""
    label = _label(card, style)
    inner = f"{label:<{BOX_WIDTH}}"
    if plain:
        return ["\u250c" + "\u2500" * BOX_WIDTH + "\u2510", f"\u2502{inner}\u2502", "\u2514" + "\u2500" * BOX_WIDTH + "\u2518"]
    colour = suit_colour(card)
    padding = " " * max(0, BOX_WIDTH - len(label))
    return [
        f"[{BORDER}]\u250c" + "\u2500" * BOX_WIDTH + f"\u2510[/]",
        f"[{BORDER}]\u2502[/][{colour}]{label}[/]{padding}[{BORDER}]\u2502[/]",
        f"[{BORDER}]\u2514" + "\u2500" * BOX_WIDTH + f"\u2518[/]",
    ]


def empty_box() -> list[str]:
    blank = " " * BOX_WIDTH
    return [
        f"[{BORDER}]\u250c" + "\u2500" * BOX_WIDTH + "\u2510[/]",
        f"[{BORDER}]\u2502" + blank + "\u2502[/]",
        f"[{BORDER}]\u2514" + "\u2500" * BOX_WIDTH + "\u2518[/]",
    ]


def card_back_box() -> list[str]:
    """A large face-down card for the local player's bottom seat."""
    fill = HIDDEN_HOLE * 2
    return [
        f"[{BORDER}]┌" + "─" * BOX_WIDTH + "┐[/]",
        f"[{BORDER}]│{fill}│[/]",
        f"[{BORDER}]└" + "─" * BOX_WIDTH + "┘[/]",
    ]


def hole_box_lines(
    hole: Sequence[int], style: str = "glyphs", *, hidden: bool = False
) -> list[str]:
    """Two large side-by-side cards for the player's prominent bottom seat."""
    if hidden or len(hole) < 2:
        boxes = [card_back_box(), card_back_box()]
    else:
        boxes = [card_box(card, style) for card in hole[:2]]
    return [" ".join(box[line] for box in boxes) for line in range(3)]


def board_box_lines(
    board: Sequence[int], style: str = "glyphs", slots: int = 5
) -> list[str]:
    """The board as three lines of side-by-side card boxes."""
    boxes = [card_box(card, style) for card in board]
    boxes += [empty_box() for _ in range(max(0, slots - len(board)))]
    return ["".join(box[line] for box in boxes) for line in range(3)]


def money(amount: int) -> str:
    return f"{amount:,}"


def result_markup(net: int) -> str:
    if net > 0:
        return f"[{ACCENT}]+{money(net)}[/]"
    if net < 0:
        return f"[{RED}]-{money(abs(net))}[/]"
    return f"[{TEXT_DIM}]even[/]"


def strength_bar(fraction: float, width: int = 10) -> str:
    """A small block bar for equity, coloured by how strong it is."""
    filled = max(0, min(width, round(fraction * width)))
    if fraction >= 0.65:
        colour = ACCENT
    elif fraction >= 0.4:
        colour = WARN
    else:
        colour = RED
    full = "\u2588" * filled
    empty = "\u2591" * (width - filled)
    return f"[{colour}]{full}[/][{BORDER}]{empty}[/]"


def assist_dots(level: int, maximum: int = 5) -> str:
    dots = "".join("\u25cf" if i <= level else "\u25cb" for i in range(maximum + 1))
    return f"[{ACCENT}]{dots}[/]"
