"""The table's building blocks.

Each widget renders itself from plain data, so the screen above only has to push
numbers in. That keeps the poker logic out of the widgets entirely, which is the
only way a TUI stays testable.
"""

from __future__ import annotations

from typing import Sequence

from rich.text import Text
from textual.color import Color
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, Input, RichLog, Sparkline, Static

from ..engine.game import LegalActions, Street
from . import theme


def _plain_len(markup: str) -> int:
    """Visible length of Rich markup, ignoring the tags."""
    length = 0
    in_tag = False
    for char in markup:
        if char == "[":
            in_tag = True
        elif char == "]":
            in_tag = False
        elif not in_tag:
            length += 1
    return length


def _seat_state_markup(
    name: str,
    stack: int,
    hole: Sequence[int],
    *,
    hidden: bool,
    folded: bool,
    all_in: bool,
    last_action: str,
    dealer: bool,
    style: str,
    hand_label: str = "",
) -> str:
    """Two lines: who and how much, then cards and what they did."""
    marker = "\u25cf " if dealer else ""
    if len(name) > 14:
        name = name[:13] + "…"
    if folded:
        name_style = f"[{theme.TEXT_DIM}]"
        stack_style = f"[{theme.TEXT_DIM}]"
    else:
        name_style = f"[{theme.TEXT}]"
        stack_style = f"[{theme.ACCENT}]"

    line1_plain = f"{marker}{name} {stack:,}"
    pad = max(1, 24 - len(line1_plain))
    line1 = f"{name_style}{marker}{name}[/]{' ' * pad}{stack_style}{stack:,}[/]"

    line2 = theme.hole_markup(hole, style, hidden=hidden)
    if hand_label and not folded:
        line2 += f" [{theme.BLUE}]{hand_label}[/]"

    if folded:
        right = f"[{theme.TEXT_DIM}]folded[/]"
    elif all_in:
        right = f"[{theme.RED}]all in[/]"
    elif last_action:
        right = f"[{theme.WARN}]{last_action}[/]"
    else:
        right = ""
    if right:
        spacing = max(1, 23 - _plain_len(line2))
        line2 += " " * spacing + right
    return f"{line1}\n{line2}"


class SeatCard(Static):
    """One player's place at the table."""

    class Activated(Message):
        def __init__(self, seat_index: int) -> None:
            self.seat_index = seat_index
            super().__init__()

    def __init__(self, seat_index: int, **kwargs) -> None:
        super().__init__("", **kwargs)
        self.seat_index = seat_index

    def show(
        self,
        *,
        name: str,
        stack: int,
        hole: Sequence[int] = (),
        hidden: bool = False,
        folded: bool = False,
        all_in: bool = False,
        last_action: str = "",
        dealer: bool = False,
        your_turn: bool = False,
        is_you: bool = False,
        style: str = "glyphs",
        hand_label: str = "",
        sitting_out: bool = False,
        prominent: bool = False,
    ) -> None:
        if sitting_out and stack <= 0:
            self.update(f"[{theme.TEXT_DIM}]{name}[/]\n[{theme.RED}]no chips[/]")
        else:
            if prominent:
                marker = "● " if dealer else ""
                status = (
                    "folded" if folded else
                    "all in" if all_in else
                    last_action or hand_label
                )
                boxes = theme.hole_box_lines(hole, style, hidden=hidden)
                if status:
                    boxes[1] += f"  [{theme.WARN}]{status}[/]"
                self.update(
                    f"[{theme.TEXT}]{marker}{name}[/]  "
                    f"[{theme.ACCENT}]{stack:,} chips[/]\n" + "\n".join(boxes)
                )
            else:
                self.update(
                    _seat_state_markup(
                        name,
                        stack,
                        hole,
                        hidden=hidden,
                        folded=folded,
                        all_in=all_in,
                        last_action=last_action,
                        dealer=dealer,
                        style=style,
                        hand_label=hand_label,
                    )
                )
        self.set_class(folded, "folded")
        self.set_class(your_turn, "active")
        self.set_class(is_you, "you")
        self.set_class(prominent, "hero-seat")
        self.set_class(stack <= 0 and not sitting_out and not all_in, "busted")

    def on_click(self, event) -> None:  # pragma: no cover - UI plumbing
        self.post_message(self.Activated(self.seat_index))


class BoardPanel(Static):
    """The community cards, the pot and the street."""

    def show(
        self,
        board: Sequence[int],
        pot: int,
        street: Street | str,
        *,
        style: str = "glyphs",
        current_bet: int = 0,
    ) -> None:
        lines = theme.board_box_lines(board, style)
        street_name = street.value if isinstance(street, Street) else str(street)
        street_line = f"[{theme.TEXT_DIM}]{street_name.upper()}[/]"
        pot_line = f"[{theme.GOLD}]\u25c6[/] [{theme.ACCENT}]POT {pot:,}[/]"
        if current_bet:
            pot_line += (
                f"   [{theme.TEXT_DIM}]\u00b7[/]   "
                f"[{theme.WARN}]TO CALL {current_bet:,}[/]"
            )
        self.update("\n".join([street_line, *lines, pot_line]))


class PokerTable(Vertical):
    """The felt in the middle of the player ring.

    Two nested frames do the work a real table's edges do: the outer border is
    the padded rail, the inner one is the edge where the felt meets it.
    """

    def compose(self):
        with Vertical(id="felt"):
            yield Static("\u25c6  O M A   P O K E R  \u25c6", id="felt-mark")
            yield BoardPanel(id="board-panel")


class Room(Static):
    """The room the table stands in: a dim carpet lit from above.

    The carpet is the widget's own text, and the ring is mounted on top of it,
    so a transparent row lets the floor show through. Textual's layers paint
    under the screen background, which is why the floor is drawn this way.

    Painted from the widget's own size, so it tiles correctly whatever shape the
    window is, windowed or fullscreen. Dots near the middle (under the table)
    catch a little more light than the ones at the edges.
    """

    SHADES = theme.FLOOR_SHADES
    def __init__(self, *children, **kwargs) -> None:
        super().__init__("", **kwargs)
        self._children = children

    def compose(self):
        yield from self._children

    def on_mount(self) -> None:
        self.call_after_refresh(self._paint)

    def on_resize(self, event) -> None:  # pragma: no cover - geometry only
        self._paint()

    def _paint(self) -> None:
        width, height = self.size.width, self.size.height
        if width <= 0 or height <= 0:
            return
        cx, cy = width / 2, height * 0.55
        text = Text()
        for y in range(height):
            for x in range(width):
                if x % 5 != (y * 2) % 5:
                    text.append(" ")
                    continue
                nx = (x - cx) / max(1.0, width * 0.62)
                ny = (y - cy) / max(1.0, height * 0.85)
                light = 1.0 - min(1.0, (nx * nx + ny * ny) ** 0.5)
                shade = self.SHADES[min(len(self.SHADES) - 1, int(light * len(self.SHADES)))]
                text.append("\u00b7", style=shade)
            text.append("\n")
        self.update(text)


class CoachPanel(Static):
    """Whatever the assist dial currently permits."""

    def show(self, advice, dial, *, hidden_reason: str = "") -> None:
        if hidden_reason:
            self.update(f"[{theme.TEXT_DIM}]{hidden_reason}[/]")
            return
        if dial.level == 0 or advice is None or advice.is_empty():
            self.update(
                f"[{theme.TEXT_DIM}]Assist is off. Press [b]A[/] for the coach, "
                f"or [b]F1[/] for help.[/]"
            )
            return

        lines: list[str] = [
            f"[{theme.ACCENT}]{theme.assist_dots(dial.level)}[/] "
            f"[{theme.TEXT_DIM}]Assist {dial.level}/5 - {dial.title}[/]"
        ]
        head: list[str] = []
        if advice.hand_name:
            head.append(f"[{theme.TEXT}]{advice.hand_name}[/]")
        if advice.equity is not None:
            head.append(
                theme.strength_bar(advice.equity)
                + f" [{theme.TEXT}]{advice.equity * 100:.0f}%[/]"
            )
        if head:
            lines.append("  ".join(head))

        if advice.headline:
            lines.append(f"[{theme.ACCENT}]{advice.headline}[/]")
            lines.append(f"[{theme.TEXT_DIM}]{advice.reason}[/]")

        for entry in advice.numbers:
            lines.append(f"[{theme.CYAN}]\u2022[/] {entry}")
        for entry in advice.chart:
            lines.append(f"[{theme.PURPLE}]\u2022[/] {entry}")
        for entry in advice.walkthrough:
            lines.append(f"[{theme.BLUE}]\u2022[/] {entry}")

        self.update("\n".join(lines))


class ActionBar(Vertical):
    """Buttons, a bet box and a one-line summary of what is legal."""

    def compose(self):
        with Horizontal(id="action-buttons"):
            yield Button("Fold", id="btn-fold", variant="error")
            yield Button("Check", id="btn-call", variant="primary")
            yield Button("Raise", id="btn-raise", variant="success")
            yield Input(placeholder="amount", id="bet-input", type="integer")
            with Horizontal(id="bet-quick"):
                yield Button("Min", id="btn-min", classes="quick")
                yield Button("\u00bd", id="btn-half", classes="quick")
                yield Button("Pot", id="btn-pot", classes="quick")
                yield Button("All in", id="btn-max", classes="quick")
        yield Static("", id="action-hint")

    # -- labels and enablement -----------------------------------------
    def set_legal(self, legal: LegalActions, *, note: str = "", enabled: bool = True) -> None:
        fold = self.query_one("#btn-fold", Button)
        call = self.query_one("#btn-call", Button)
        raise_button = self.query_one("#btn-raise", Button)

        if legal.can_check:
            call.label = "Check"
        elif legal.can_call:
            call.label = f"Call {legal.call_amount:,}"
        else:
            call.label = "Check"

        if legal.can_bet:
            raise_button.label = f"Bet {legal.min_bet:,}+"
        elif legal.can_raise:
            raise_button.label = f"Raise {legal.min_raise_to:,}+"
        else:
            raise_button.label = "Raise"

        fold.disabled = not (legal.can_fold and enabled)
        call.disabled = not ((legal.can_check or legal.can_call) and enabled)
        raise_button.disabled = not ((legal.can_bet or legal.can_raise) and enabled)
        self.query_one("#bet-input", Input).disabled = not enabled

        hint = self.query_one("#action-hint", Static)
        if note:
            hint.update(f"[{theme.WARN}]{note}[/]")
        else:
            hint.update(
                f"[{theme.TEXT_DIM}]F fold · C check/call · R or B raise · "
                f"A assist · M music · ? glossary · L log · Q leave hand[/]"
            )

    # -- the bet box ---------------------------------------------------
    def amounts(self) -> int:
        raw = self.query_one("#bet-input", Input).value.strip()
        try:
            return max(0, int(raw))
        except ValueError:
            return 0

    def suggest(self, amount: int) -> None:
        self.query_one("#bet-input", Input).value = str(max(0, int(amount)))

    def clear_amount(self) -> None:
        self.query_one("#bet-input", Input).value = ""

    def focus_amount(self) -> None:
        box = self.query_one("#bet-input", Input)
        box.focus()
        box.cursor_position = len(box.value)

    def blur_amount(self) -> None:
        self.query_one("#bet-input", Input).blur()


class MusicChip(Horizontal):
    """The now-playing chip in the top right: click it for the music panel."""

    class OpenPanel(Message):
        pass

    class CycleSource(Message):
        pass

    def compose(self):
        yield Sparkline([0.0] * 12, id="music-bars")
        yield Static("Off", id="music-chip")
        yield Button("↻", id="music-source", tooltip="Next music source")

    def show(self, text: str, levels: Sequence[float], live: bool) -> None:
        bars = self.query_one("#music-bars", Sparkline)
        data = list(levels) or [0.0] * 12
        if live:
            bars.data = data
            bars.min_color = Color.parse(theme.ACCENT_DIM)
            bars.max_color = Color.parse(theme.ACCENT)
        else:
            bars.data = [0.02] * len(data)
            bars.min_color = Color.parse(theme.BORDER)
            bars.max_color = Color.parse(theme.BORDER)
        self.query_one("#music-chip", Static).update(
            f"[{theme.ACCENT}]\u266a[/] [{theme.TEXT}]{text}[/]"
        )

    def on_click(self, event) -> None:  # pragma: no cover - UI plumbing
        if isinstance(event.widget, Button):
            return
        self.post_message(self.OpenPanel())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "music-source":
            event.stop()
            self.post_message(self.CycleSource())


class LogPanel(RichLog):
    """A running commentary of the hand, most recent last."""

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("max_lines", 300)
        kwargs.setdefault("wrap", True)
        kwargs.setdefault("markup", True)
        kwargs.setdefault("auto_scroll", True)
        super().__init__(**kwargs)

    def add_event(self, text: str, kind: str = "info") -> None:
        colour = {
            "info": theme.TEXT_DIM,
            "action": theme.TEXT,
            "win": theme.ACCENT,
            "all_in": theme.RED,
            "street": theme.CYAN,
        }.get(kind, theme.TEXT_DIM)
        self.write(Text.from_markup(f"[{colour}]{text}[/]"))


def key_value_lines(pairs: Sequence[tuple[str, str]]) -> str:
    """Two-column, aligned text for panels."""
    if not pairs:
        return ""
    width = max(len(str(key)) for key, _ in pairs)
    return "\n".join(f"[{theme.TEXT}]{key:<{width}}[/]  [{theme.TEXT_DIM}]{value}[/]"
                     for key, value in pairs)
