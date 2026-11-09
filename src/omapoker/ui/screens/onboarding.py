"""First run: who are you, and how much poker have you played?

The answer sets the assist dial's starting point. It is a starting point only -
the dial is always editable, mid-hand, from the table.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Vertical
from textual.widgets import Button, Footer, Input, RadioButton, RadioSet, Static

from ...data import EXPERIENCE_LABELS
from .. import theme
from .base import BaseScreen, ChoiceScreen, ConfirmScreen

ORDER = ("newcomer", "casual", "skilled", "advanced")


class OnboardingScreen(BaseScreen):
    """Name and experience, then straight to the menu."""

    BINDINGS = [Binding("ctrl+q", "quit", "Quit", show=True)]

    def compose(self) -> ComposeResult:
        with Center():
            with Vertical(id="menu-card"):
                yield Static("OmaPoker", id="menu-title")
                yield Static(
                    f"[{theme.TEXT_DIM}]Texas Hold'em, with a coach that knows "
                    f"exactly how much you want to be told.[/]",
                    id="menu-sub",
                )
                yield Static("")
                yield Static(f"[{theme.TEXT}]What should we call you?[/]")
                yield Input(placeholder="your name", id="name-input")
                yield Static("")
                yield Static(f"[{theme.TEXT}]How much poker have you played?[/]")
                yield RadioSet(
                    *(
                        RadioButton(
                            EXPERIENCE_LABELS[level],
                            value=(level == "casual"),
                            id=f"exp-{level}",
                        )
                        for level in ORDER
                    ),
                    id="experience",
                )
                yield Static("")
                yield Static(
                    f"[{theme.TEXT_DIM}]Every new player starts with "
                    f"100,000 chips. You can change any of this later in "
                    f"Settings.[/]"
                )
                yield Button("Create my profile", id="create", variant="success")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#name-input", Input).focus()

    # -- buttons and keys ----------------------------------------------
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "create":
            self.create()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.create()

    def action_quit(self) -> None:
        self.app.exit()

    def create(self) -> None:
        name = self.query_one("#name-input", Input).value.strip()
        if not name:
            self.app.notify("Give yourself a name first.", severity="warning")
            return
        index = self.query_one("#experience", RadioSet).pressed_index
        level = ORDER[index] if isinstance(index, int) and 0 <= index < len(ORDER) else "casual"
        profile = self.app.create_profile(name, level)
        self.app.notify(
            f"Welcome, {profile.name}. {EXPERIENCE_LABELS[level].split(' - ')[0]} "
            f"it is; assist starts at {profile.assist_level}/5.",
            title="Profile created",
        )
        self.app.open_menu()


class ProfileScreen(BaseScreen):
    """Switch between profiles, add one, or remove one."""

    def compose(self) -> ComposeResult:
        profiles = self.app.store.list_profiles()
        active = self.app.store.active_profile_id()
        yield Static(f"[{theme.ACCENT}][b]Profiles[/b][/]", id="panel-title")
        with Center():
            with Vertical(id="menu-card"):
                yield Static(
                    f"[{theme.TEXT_DIM}]Each profile keeps its own bankroll, "
                    f"statistics and music settings.[/]"
                )
                for profile in profiles:
                    marker = "\u25cf " if profile.id == active else ""
                    chips = self.app.store.bankroll_for(profile.id).chips
                    yield Button(
                        f"{marker}{profile.name}  ({chips:,} chips, "
                        f"assist {profile.assist_level}/5)",
                        id=f"use-{profile.id}",
                    )
                yield Button("New profile", id="new", variant="success")
                yield Button("Delete a profile", id="delete", variant="error")
                yield Button("Back", id="back")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        identifier = event.button.id or ""
        if identifier == "back":
            self.app.pop_screen()
        elif identifier == "new":
            self.app.push_screen(OnboardingScreen())
        elif identifier == "delete":
            self.choose_delete()
        elif identifier.startswith("use-"):
            self.app.switch_profile(identifier[4:])

    def choose_delete(self) -> None:
        profiles = self.app.store.list_profiles()
        if len(profiles) <= 1:
            self.app.notify("Keep at least one profile.", severity="warning")
            return
        options = [(p.id, f"{p.name}") for p in profiles]
        self.app.push_screen(
            ChoiceScreen(
                "Delete which profile?",
                options,
                detail="This also removes its bankroll and statistics.",
            ),
            self.confirm_delete,
        )

    def confirm_delete(self, choice: str | None) -> None:
        if not choice:
            return
        profile = self.app.store.load_profile(choice)
        name = profile.name if profile else choice
        self.app.push_screen(
            ConfirmScreen(
                f"Delete {name}?",
                detail="Bankroll, statistics and identity go with it. Backups stay.",
                danger=True,
            ),
            lambda ok: self._delete(choice, ok),
        )

    def _delete(self, identifier: str, ok: bool | None) -> None:
        if not ok:
            return
        self.app.store.delete_profile(identifier)
        self.app.notify("Profile deleted.")
        self.app.open_menu()
