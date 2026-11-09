"""Choosing a table: the buy-in ladder, the opposition, and hot-seat setup.

The ladder is the whole economy of the game in one list. Your buy-in *is* your
stack, blinds are always 1/100th of it, and every table is 100 big blinds deep,
so moving up a rung changes the stakes without changing the game.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, Vertical
from textual.widgets import Button, Footer, Input, Label, ListItem, ListView, Select, Static

from ...data import MIN_BUY_IN, STARTING_CHIPS, ladder
from ...engine.bots import PROFILES
from ...session import Mode
from .. import theme
from .base import BaseScreen

DIFFICULTY_OPTIONS = [
    ("Easy - loose and forgiving", "easy"),
    ("Medium - sensible, beatable", "medium"),
    ("Hard - punishes mistakes", "hard"),
    ("Pro - no mercy", "pro"),
]

OPPONENT_OPTIONS = [
    (f"{count} opponent{'s' if count > 1 else ''}"
     + (" (heads-up)" if count == 1 else ""), str(count))
    for count in range(1, 9)
]

MAX_HOTSEAT = 6


class TablePickerScreen(BaseScreen):
    """Pick a rung of the ladder and sit down."""

    BINDINGS = [Binding("enter", "start", "Sit down", show=True)]

    def __init__(
        self,
        mode: Mode = Mode.SOLO,
        human_names: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.mode = mode
        self.human_names = human_names or [self._hero_name()]

    def _hero_name(self) -> str:
        return self.app.player.profile.name

    # -- layout --------------------------------------------------------
    def compose(self) -> ComposeResult:
        chips = self.app.player.bankroll.chips
        mode_label = {
            Mode.SOLO: "Playing against bots",
            Mode.HOTSEAT: f"Hot-seat, {len(self.human_names)} players: "
            + ", ".join(self.human_names),
            Mode.HOST: "Hosting a LAN table",
        }.get(self.mode, str(self.mode.value))

        yield Static(
            f"[{theme.ACCENT}][b]Choose a table[/b][/]  "
            f"[{theme.TEXT_DIM}]{mode_label}[/]",
            id="panel-title",
        )
        with Vertical(id="ladder-shell"):
            with Vertical(id="menu-card"):
                yield Static(
                    f"[{theme.TEXT}]Bankroll[/]  [{theme.ACCENT}]{chips:,}[/]  "
                    f"[{theme.TEXT_DIM}]a buy-in leaves your bankroll and comes "
                    f"back when you leave the table[/]",
                    id="ladder-bankroll",
                )
                if chips < MIN_BUY_IN:
                    yield Static(
                        f"[{theme.WARN}]Nothing on the ladder is affordable at "
                        f"{chips:,} chips. Settle the books and sit back down "
                        f"with {STARTING_CHIPS:,}.[/]",
                        id="ladder-broke",
                    )
                    yield Button(
                        f"Reset bankroll to {STARTING_CHIPS:,}",
                        id="rescue",
                        variant="warning",
                    )
                with Horizontal(id="ladder-controls"):
                    yield Select(
                        DIFFICULTY_OPTIONS,
                        value="medium",
                        prompt="Bot skill",
                        allow_blank=False,
                        id="difficulty",
                    )
                    if self.mode in (Mode.SOLO, Mode.HOST):
                        yield Select(
                            OPPONENT_OPTIONS,
                            value="4",
                            prompt="Opponents",
                            allow_blank=False,
                            id="opponents",
                        )
                    yield Button("Sit down", id="start", variant="success")
                    yield Button("Back", id="back")
                yield ListView(id="ladder-list")
        yield Footer()

    def on_mount(self) -> None:
        self._fill_ladder()

    def _fill_ladder(self) -> None:
        view = self.query_one("#ladder-list", ListView)
        chips = self.app.player.bankroll.chips
        rungs = list(reversed(ladder()))
        view.clear()
        for level in rungs:
            affordable = level.buy_in <= chips
            label = (
                f"[{theme.ACCENT if affordable else theme.TEXT_DIM}]"
                f"{level.tier:<10}[/] "
                f"[{theme.TEXT if affordable else theme.TEXT_DIM}]"
                f"buy-in {level.buy_in:>7,}[/]  "
                f"[{theme.TEXT_DIM}]blinds {level.small_blind:,}/"
                f"{level.big_blind:,}  100 BB deep"
                f"{'' if affordable else '  locked'}[/]"
            )
            item = ListItem(Static(label), id=f"level-{level.buy_in}")
            item.disabled = not affordable
            view.append(item)
        # Highlight the richest table you can afford.
        for index, level in enumerate(rungs):
            if level.buy_in <= chips:
                view.index = index
                break
        view.focus()

    # -- helpers -------------------------------------------------------
    def selected_level(self):
        view = self.query_one("#ladder-list", ListView)
        index = view.index
        rungs = list(reversed(ladder()))
        if index is None or not 0 <= index < len(rungs):
            return None
        return rungs[index]

    def selected_difficulty(self) -> str:
        value = self.query_one("#difficulty", Select).value
        return value if isinstance(value, str) else "medium"

    def selected_opponents(self) -> int:
        selectors = self.query("#opponents")
        if not selectors:
            return 4
        value = selectors.first(Select).value
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 4

    # -- events --------------------------------------------------------
    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.action_start()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "start":
            self.action_start()
        elif event.button.id == "rescue":
            self.action_rescue()
        elif event.button.id == "back":
            self.action_back()

    def action_rescue(self) -> None:
        """Settle the books so a dead-end bankroll can sit down again."""
        self.app.reset_bankroll()
        self.app.notify(f"Bankroll reset to {STARTING_CHIPS:,} chips.")
        # The screen was built for the old bankroll: build it again for the new.
        self.app.switch_screen(
            TablePickerScreen(mode=self.mode, human_names=self.human_names)
        )

    def action_start(self) -> None:
        level = self.selected_level()
        if level is None:
            self.app.notify("Pick a table from the list first.", severity="warning")
            return
        if level.buy_in > self.app.player.bankroll.chips:
            self.app.notify(
                f"A {level.buy_in:,} buy-in needs {level.buy_in:,} chips; "
                f"you have {self.app.player.bankroll.chips:,}.",
                severity="warning",
            )
            return
        profile = PROFILES[self.selected_difficulty()]
        names = self.human_names
        if self.mode is Mode.HOST and self.app.host_server is not None:
            # Players can arrive or leave while the host chooses the buy-in.
            names = [self.app.player.profile.name] + [
                conn.name for conn in self.app.host_server.players()
            ]
        self.app.start_session(
            level,
            mode=self.mode,
            human_names=names,
            bot_level=profile.level,
            bot_count=self.selected_opponents(),
        )


class HotSeatSetupScreen(BaseScreen):
    """How many humans, and what are they called?"""

    def compose(self) -> ComposeResult:
        hero = self.app.player.profile.name
        yield Static(f"[{theme.ACCENT}][b]Hot-seat[/b][/]", id="panel-title")
        with Center():
            with Vertical(id="menu-card"):
                yield Static(
                    f"[{theme.TEXT_DIM}]Everyone shares this screen. Before each "
                    f"turn OmaPoker shows a cover screen so nobody sees the wrong "
                    f"cards.[/]"
                )
                yield Select(
                    [(f"{n} players", str(n)) for n in range(2, MAX_HOTSEAT + 1)],
                    value="2",
                    prompt="How many?",
                    allow_blank=False,
                    id="seat-count",
                )
                yield Static(f"[{theme.TEXT}]Names[/]")
                for position in range(MAX_HOTSEAT):
                    yield Input(
                        value=hero if position == 0 else "",
                        placeholder=f"player {position + 1}",
                        id=f"player-{position}",
                    )
                yield Button("Choose a table", id="continue", variant="success")
                yield Button("Back", id="back")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_inputs()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "seat-count":
            self.refresh_inputs()

    def refresh_inputs(self) -> None:
        count = self.seat_count()
        for position in range(MAX_HOTSEAT):
            widget = self.query_one(f"#player-{position}", Input)
            widget.display = position < count
            widget.disabled = position >= count

    def seat_count(self) -> int:
        value = self.query_one("#seat-count", Select).value
        try:
            return max(2, min(MAX_HOTSEAT, int(value)))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 2

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back":
            self.action_back()
        elif event.button.id == "continue":
            names: list[str] = []
            count = self.seat_count()
            for position in range(count):
                raw = self.query_one(f"#player-{position}", Input).value.strip()
                names.append(raw or f"Player {position + 1}")
            if len(set(names)) != len(names):
                self.app.notify("Give everybody a different name.", severity="warning")
                return
            self.app.push_screen(TablePickerScreen(Mode.HOTSEAT, names))


__all__ = ["HotSeatSetupScreen", "TablePickerScreen", "Label"]
