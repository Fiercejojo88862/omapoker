"""Shared screen scaffolding: base bindings, panels and small modals."""

from __future__ import annotations

from typing import Iterable, Sequence

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Middle, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Footer, Static

from .. import theme


class BaseScreen(Screen):
    """Every OmaPoker screen: consistent keys, consistent escape hatch."""

    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("f1", "help", "Help", show=False),
        Binding("m", "music", "Music", show=False),
        Binding("question_mark", "glossary", "Glossary", show=False),
    ]

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_help(self) -> None:
        self.app.open_help()

    def action_music(self) -> None:
        self.app.open_music()

    def action_glossary(self) -> None:
        self.app.open_glossary()


class PanelScreen(BaseScreen):
    """A titled, scrollable panel with a footer."""

    title = "Panel"
    subtitle = ""

    def body(self) -> ComposeResult:  # pragma: no cover - overridden
        yield Static("")

    def compose(self) -> ComposeResult:
        head = f"[{theme.ACCENT}]{self.title}[/]"
        if self.subtitle:
            head += f"  [{theme.TEXT_DIM}]{self.subtitle}[/]"
        yield Static(head, id="panel-title")
        with VerticalScroll(id="panel-scroll"):
            yield from self.body()
        yield Footer()


class MessageScreen(ModalScreen[None]):
    """A modal that says one thing and closes."""

    BINDINGS = [Binding("escape", "close", "Close"), Binding("enter", "close", "Close")]

    def __init__(self, title: str, lines: Sequence[str] = (), *, ok: str = "Close") -> None:
        super().__init__()
        self._title = title
        self._lines = list(lines)
        self._ok = ok

    def compose(self) -> ComposeResult:
        with Middle(id="overlay"):
            with Vertical(id="overlay-card"):
                yield Static(f"[{theme.ACCENT}][b]{self._title}[/][/]")
                for line in self._lines:
                    yield Static(line)
                yield Button(self._ok, id="ok", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class ConfirmScreen(ModalScreen[bool]):
    """``await push_screen(ConfirmScreen(...))`` returns True when confirmed."""

    BINDINGS = [
        Binding("escape", "no", "Cancel"),
        Binding("enter", "yes", "Confirm"),
        Binding("y", "yes", "Yes", show=False),
        Binding("n", "no", "No", show=False),
    ]

    def __init__(self, question: str, *, detail: str = "", danger: bool = False) -> None:
        super().__init__()
        self._question = question
        self._detail = detail
        self._danger = danger

    def compose(self) -> ComposeResult:
        with Middle(id="overlay"):
            with Vertical(id="overlay-card"):
                yield Static(f"[{theme.ACCENT}][b]{self._question}[/][/]")
                if self._detail:
                    yield Static(f"[{theme.TEXT_DIM}]{self._detail}[/]")
                yield Button(
                    "Confirm", id="yes", variant="error" if self._danger else "primary"
                )
                yield Button("Cancel", id="no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class ChoiceScreen(ModalScreen[str | None]):
    """Pick one option from a list of ``(value, label)`` pairs."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, options: Iterable[tuple[str, str]], *, detail: str = "") -> None:
        super().__init__()
        self._title = title
        self._options = list(options)
        self._detail = detail

    def compose(self) -> ComposeResult:
        with Middle(id="overlay"):
            with Vertical(id="overlay-card"):
                yield Static(f"[{theme.ACCENT}][b]{self._title}[/][/]")
                if self._detail:
                    yield Static(f"[{theme.TEXT_DIM}]{self._detail}[/]")
                for value, label in self._options:
                    yield Button(label, id=f"choice-{value}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        identifier = event.button.id or ""
        self.dismiss(identifier.removeprefix("choice-"))

    def action_cancel(self) -> None:
        self.dismiss(None)


class PrivacyScreen(ModalScreen[None]):
    """Hot-seat hand hiding: pass the keyboard without showing the cards."""

    BINDINGS = [Binding("enter", "ready", "Ready"), Binding("space", "ready", "Ready")]

    def __init__(self, name: str) -> None:
        super().__init__()
        self._name = name

    def compose(self) -> ComposeResult:
        with Middle(id="overlay"):
            with Vertical(id="overlay-card"):
                yield Static(
                    f"[{theme.ACCENT}][b]Pass the keyboard[/b][/]\n"
                    f"[{theme.TEXT}]{self._name}[/], it is your turn.",
                    id="overlay-text",
                )
                yield Static(
                    f"[{theme.TEXT_DIM}]Cards stay hidden until you say you are ready.[/]"
                )
                yield Button("Ready", id="ready", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_ready(self) -> None:
        self.dismiss(None)


class QuitScreen(MessageScreen):
    """Confirmation shown when leaving with a live session."""


__all__ = [
    "BaseScreen",
    "Center",
    "ChoiceScreen",
    "ConfirmScreen",
    "MessageScreen",
    "PanelScreen",
    "PrivacyScreen",
    "QuitScreen",
]
