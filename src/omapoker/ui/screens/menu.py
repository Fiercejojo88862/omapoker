"""The main menu: the one screen that has to explain the whole app at a glance."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Middle, Vertical, VerticalScroll
from textual.widgets import Button, Footer, Static

from ...data import best_table, code_matches_key
from ...engine.game import Street
from .. import theme
from .base import BaseScreen, ConfirmScreen

QUICK_KEYS = (
    ("p", "play"),
    ("o", "hotseat"),
    ("n", "host"),
    ("j", "join"),
    ("s", "stats"),
    ("l", "lessons"),
    ("t", "settings"),
    ("u", "profiles"),
)


class MainMenuScreen(BaseScreen):
    """Play, learn, listen, or leave."""

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("escape", "quit", "Quit"),
        *(Binding(key, action, "", show=False) for key, action in QUICK_KEYS),
    ]

    def compose(self) -> ComposeResult:
        app = self.app
        player = app.player
        chips = player.bankroll.chips if player else 0
        table = best_table(chips)
        dial = app.dial

        with VerticalScroll(id="menu-shell"):
            with Vertical(id="menu-card"):
                yield Static("OmaPoker", id="menu-title")
                yield Static("Texas Hold'em, Omarchy only", id="menu-sub")
                yield Static(
                    f"[{theme.TEXT}]Player[/]   [{theme.ACCENT}]{player.profile.name}[/]"
                    f"   [{theme.TEXT_DIM}]{player.profile.experience}[/]\n"
                    f"[{theme.TEXT}]Chips [/]   [{theme.ACCENT}]{chips:,}[/]"
                    f"   [{theme.TEXT_DIM}]"
                    f"{('richest table: ' + table.name) if table else 'not enough for a table yet'}"
                    f"[/]\n"
                    f"[{theme.TEXT}]Assist[/]   {theme.assist_dots(dial.level)} "
                    f"[{theme.TEXT_DIM}]{dial.level}/5 - {dial.title}[/]"
                )
                with Grid(id="menu-actions"):
                    yield Button("Play against bots", id="btn-solo", variant="success")
                    yield Button("Hot-seat (2-6 players)", id="btn-hotseat")
                    yield Button("Host a LAN table", id="btn-host")
                    yield Button("Join a LAN table", id="btn-join")
                    yield Button("Statistics and leaks", id="btn-stats")
                    yield Button("Lessons and glossary", id="btn-lessons")
                    yield Button("Music", id="btn-music")
                    yield Button("Settings", id="btn-settings")
                    yield Button("Switch profile", id="btn-profiles")
                    yield Button("Quit", id="btn-quit", variant="error")
        yield Footer()

    # -- buttons -------------------------------------------------------
    def on_button_pressed(self, event: Button.Pressed) -> None:
        identifier = event.button.id or ""
        if identifier == "btn-solo":
            self.action_play()
        elif identifier == "btn-hotseat":
            self.action_hotseat()
        elif identifier == "btn-host":
            self.action_host()
        elif identifier == "btn-join":
            self.action_join()
        elif identifier == "btn-stats":
            self.action_stats()
        elif identifier == "btn-lessons":
            self.action_lessons()
        elif identifier == "btn-music":
            self.action_music()
        elif identifier == "btn-settings":
            self.action_settings()
        elif identifier == "btn-profiles":
            self.action_profiles()
        elif identifier == "btn-quit":
            self.action_quit()

    # -- actions -------------------------------------------------------
    def action_play(self) -> None:
        self.app.open_ladder()

    def action_hotseat(self) -> None:
        self.app.open_hotseat_setup()

    def action_host(self) -> None:
        self.app.open_host()

    def action_join(self) -> None:
        self.app.open_join()

    def action_stats(self) -> None:
        self.app.open_stats()

    def action_lessons(self) -> None:
        self.app.open_lessons()

    def action_settings(self) -> None:
        self.app.open_settings()

    def action_profiles(self) -> None:
        from .onboarding import ProfileScreen

        self.app.push_screen(ProfileScreen())

    def action_quit(self) -> None:
        self.app.request_quit()


class ResumeScreen(BaseScreen):
    """Shown when a table is already running and the player returns to it."""

    def compose(self) -> ComposeResult:
        session = self.app.session
        if session is None:
            yield Static("No table is running.")
        else:
            state = session.state()
            lines = [f"Level {session.level.name}"]
            for seat in session.seats:
                lines.append(f"{seat.name}: {seat.stack:,}")
            with Middle():
                with Vertical(id="menu-card"):
                    yield Static(f"[{theme.ACCENT}]Table in progress[/]")
                    for line in lines:
                        yield Static(line)
                    yield Button("Return to the table", id="resume", variant="success")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "resume":
            self.app.open_table()


def hand_is_live(session) -> bool:
    """Small helper the app uses to decide whether leaving needs confirmation."""
    return bool(session and session.hand and session.hand.street is not Street.COMPLETE)


__all__ = ["MainMenuScreen", "ResumeScreen", "ConfirmScreen", "hand_is_live", "code_matches_key"]
