"""The side panels: music, statistics, lessons, glossary, settings and help.

These are ordinary screens pushed on top of whatever is already running, so the
table stays underneath and pressing Escape puts you straight back into the hand.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Footer,
    Input,
    Label,
    ListItem,
    ListView,
    Markdown,
    Select,
    Static,
    Switch,
)

from ...coach import LEVEL_SUMMARY, LEVEL_TITLES, find_leaks, report_text, review_hand
from ...coach.dial import MAX_LEVEL, MIN_LEVEL
from ...data import EXPERIENCE_LEVELS, EXPERIENCE_LABELS, STARTING_CHIPS, crypto_available
from ...music import OFF
from .. import theme
from .base import BaseScreen, ConfirmScreen, MessageScreen, PanelScreen

SOURCE_LABELS = {
    "newcomer": "Newcomer",
    "casual": "Casual",
    "skilled": "Skilled",
    "advanced": "Advanced",
}


# --------------------------------------------------------------------------
# assist dial
# --------------------------------------------------------------------------

class AssistScreen(ModalScreen[int | None]):
    """Change how much the coach is allowed to say."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        *(Binding(str(level), f"pick({level})", str(level), show=False)
          for level in range(MIN_LEVEL, MAX_LEVEL + 1)),
    ]

    def __init__(self, current: int) -> None:
        super().__init__()
        self._current = current

    def compose(self) -> ComposeResult:
        with Center(id="overlay"):
            with Vertical(id="overlay-card"):
                yield Static(f"[{theme.ACCENT}][b]Assist dial[/b][/]")
                yield Static(
                    f"[{theme.TEXT_DIM}]Changeable at any time, even mid-hand. "
                    f"Each level adds to the one below it.[/]"
                )
                for level in range(MIN_LEVEL, MAX_LEVEL + 1):
                    marker = "\u25cf" if level == self._current else "\u25cb"
                    yield Button(
                        f"{marker} {level}  {LEVEL_TITLES[level]} - {LEVEL_SUMMARY[level]}",
                        id=f"level-{level}",
                        variant="primary" if level == self._current else "default",
                    )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        identifier = event.button.id or ""
        if identifier.startswith("level-"):
            self.dismiss(int(identifier.split("-", 1)[1]))

    def action_pick(self, level: int) -> None:
        self.dismiss(level)

    def action_cancel(self) -> None:
        self.dismiss(None)


# --------------------------------------------------------------------------
# help
# --------------------------------------------------------------------------

class HelpScreen(PanelScreen):
    """Keys, rules and a reminder of what the coach is doing."""

    title = "Help"

    def body(self) -> ComposeResult:
        yield Static(f"[{theme.ACCENT}][b]Playing[/b][/]", classes="section-title")
        yield Static(
            f"[b]F[/] fold   [b]C[/] check or call   [b]R[/] raise   [b]B[/] bet   "
            f"[b]X[/] all in\n"
            f"[b]Enter[/] confirm an amount   [b]Esc[/] cancel   "
            f"[b]N[/] next hand   [b]V[/] review the last hand\n"
            f"[b]A[/] assist dial   [b]M[/] music   [b]?[/] glossary   "
            f"[b]L[/] hide the log   [b]Q[/] leave the table\n"
            f"[b]Ctrl+Q[/] quit OmaPoker"
        )
        yield Static(f"[{theme.ACCENT}][b]The table[/b][/]", classes="section-title")
        yield Static(
            "A buy-in is your stack, and blinds are always one hundredth of it, so "
            "every table is 100 big blinds deep. Chips leave your bankroll when you "
            "sit down and come back when you leave.\n"
            "No-Limit Hold'em: two cards each, five shared cards, best five-card hand "
            "wins. You can bet or raise any amount up to your whole stack."
        )
        yield Static(f"[{theme.ACCENT}][b]The coach[/b][/]", classes="section-title")
        yield Static(
            f"[{theme.TEXT_DIM}]The assist dial decides how much OmaPoker says. The "
            f"bots never look at it, so turning it up does not make the game "
            f"easier - it only makes you better.[/]"
        )
        for level in range(MIN_LEVEL, MAX_LEVEL + 1):
            yield Static(
                f"[{theme.ACCENT}]{level}[/] [{theme.TEXT}]{LEVEL_TITLES[level]}[/] "
                f"[{theme.TEXT_DIM}]{LEVEL_SUMMARY[level]}[/]"
            )
        yield Static(f"[{theme.ACCENT}][b]Music[/b][/]", classes="section-title")
        yield Static(
            "OmaPoker never streams music itself. It drives whatever player you "
            "already use (Apple Music in Firefox, Spotify, or its own mpv player for "
            "local files). Cycle sources with the chip in the top right, or from the "
            "music panel with C.\n"
            "Space plays and pauses, N and P skip, [ and ] change the volume."
        )


# --------------------------------------------------------------------------
# glossary
# --------------------------------------------------------------------------

class GlossaryScreen(BaseScreen):
    """Every term the coach might use, searchable."""

    BINDINGS = [Binding("escape", "back", "Back")]

    def compose(self) -> ComposeResult:
        yield Static(
            f"[{theme.ACCENT}][b]Glossary[/b][/]  "
            f"[{theme.TEXT_DIM}]type to search[/]",
            id="panel-title",
        )
        yield Input(placeholder="search terms", id="glossary-search")
        with VerticalScroll(id="panel-scroll"):
            yield Static("", id="glossary-body")
        yield Footer()

    def on_mount(self) -> None:
        self.show_terms()
        self.query_one("#glossary-search", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "glossary-search":
            self.show_terms(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.show_terms(event.value)

    def show_terms(self, term: str = "") -> None:
        entries = self.app.content.search(term)
        if not entries:
            self.query_one("#glossary-body", Static).update(
                f"[{theme.TEXT_DIM}]Nothing matched {term!r}.[/]"
            )
            return
        lines = [
            f"[{theme.ACCENT}][b]{name}[/b][/]\n[{theme.TEXT}]{text}[/]"
            for name, text in entries
        ]
        self.query_one("#glossary-body", Static).update("\n\n".join(lines))


# --------------------------------------------------------------------------
# lessons
# --------------------------------------------------------------------------

class LessonsScreen(BaseScreen):
    """Short lessons, chosen for the level the player picked at sign-up."""

    def compose(self) -> ComposeResult:
        level = self.app.player.profile.experience
        lessons = self.app.content.lessons_for(level)
        yield Static(
            f"[{theme.ACCENT}][b]Lessons[/b][/]  "
            f"[{theme.TEXT_DIM}]for a {SOURCE_LABELS.get(level, level)} player[/]",
            id="panel-title",
        )
        with Horizontal(id="bottom-rows"):
            yield ListView(
                *(
                    ListItem(
                        Static(
                            f"[{theme.TEXT}]{lesson.get('title', lesson.get('id'))}[/]\n"
                            f"[{theme.TEXT_DIM}]{lesson.get('level', '')}[/]"
                        ),
                        id=f"lesson-{lesson.get('id')}",
                    )
                    for lesson in lessons
                ),
                id="lessons-list",
            )
            with VerticalScroll(id="lesson-body"):
                yield Markdown("", id="lesson-markdown")
        yield Footer()

    def on_mount(self) -> None:
        view = self.query_one("#lessons-list", ListView)
        if view.children:
            view.index = 0
            view.focus()
            self.show_lesson(0)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is not None:
            self.show_lesson(event.item.id)

    def show_lesson(self, identifier) -> None:
        lesson_id = str(identifier or "")
        if lesson_id.startswith("lesson-"):
            lesson_id = lesson_id.split("-", 1)[1]
        lesson = self.app.content.lesson(lesson_id)
        body = ""
        if lesson:
            body = f"# {lesson.get('title', lesson_id)}\n\n{lesson.get('body', '')}"
        self.query_one("#lesson-markdown", Markdown).update(body)


# --------------------------------------------------------------------------
# statistics and leaks
# --------------------------------------------------------------------------

class StatsScreen(PanelScreen):
    """Your numbers, and what the coach makes of them."""

    title = "Statistics"

    def body(self) -> ComposeResult:
        player = self.app.player
        stats = player.stats
        dial = self.app.dial

        yield Static(
            f"[{theme.TEXT}]Chips[/] [{theme.ACCENT}]{player.bankroll.chips:,}[/]  "
            f"[{theme.TEXT_DIM}]hands recorded: {stats.hands}[/]"
        )
        yield Static(f"[{theme.ACCENT}][b]Rates[/b][/]", classes="section-title")
        for name, value in stats.summary_lines():
            yield Static(f"[{theme.TEXT}]{name:<22}[/] [{theme.ACCENT}]{value}[/]")

        yield Static(f"[{theme.ACCENT}][b]Leaks[/b][/]", classes="section-title")
        leaks = find_leaks(stats, dial)
        if not leaks:
            yield Static(
                f"[{theme.TEXT_DIM}]Not enough hands yet, or nothing obviously "
                f"wrong. Play a few more and look again.[/]"
            )
        for leak in leaks:
            colour = {
                "good": theme.ACCENT,
                "minor": theme.WARN,
                "major": theme.RED,
            }.get(leak.severity, theme.TEXT)
            yield Static(
                f"[{colour}]{leak.title}[/]  [{theme.TEXT_DIM}]{leak.numbers}[/]\n"
                f"[{theme.TEXT}]{leak.detail}[/]"
            )
        yield Static(f"[{theme.ACCENT}][b]Full report[/b][/]", classes="section-title")
        yield Static(f"[{theme.TEXT_DIM}]{report_text(stats, dial)}[/]")

        if stats.by_table:
            yield Static(f"[{theme.ACCENT}][b]By table[/b][/]", classes="section-title")
            for table, net in sorted(stats.by_table.items(), key=lambda kv: -kv[1]):
                yield Static(
                    f"[{theme.TEXT}]{table:<18}[/] "
                    f"{theme.result_markup(net)}"
                    f"[{theme.TEXT_DIM}]  over {stats.by_level.get(table, 0)} hands[/]"
                )
        if stats.by_level:
            yield Static(
                f"[{theme.ACCENT}][b]By opponent skill[/b][/]", classes="section-title"
            )
            for level, net in sorted(stats.by_level.items(), key=lambda kv: -kv[1]):
                yield Static(
                    f"[{theme.TEXT}]{level:<18}[/] {theme.result_markup(net)}"
                    f"[{theme.TEXT_DIM}]  over {stats.by_table.get(level, 0)} hands[/]"
                )


# --------------------------------------------------------------------------
# music
# --------------------------------------------------------------------------

class MusicScreen(BaseScreen):
    """Sources, transport, and the local library."""

    BINDINGS = [
        Binding("space", "toggle", "Play/pause", show=True),
        Binding("n", "next", "Next", show=True),
        Binding("p", "previous", "Previous", show=True),
        Binding("c", "cycle", "Next source", show=True),
        Binding("left_square_bracket", "volume(-5)", "Vol -", show=False),
        Binding("right_square_bracket", "volume(5)", "Vol +", show=False),
        Binding("s", "shuffle", "Shuffle", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._tracks: list = []

    def compose(self) -> ComposeResult:
        music = self.app.music
        yield Static(
            f"[{theme.ACCENT}][b]Music[/b][/]  "
            f"[{theme.TEXT_DIM}]OmaPoker drives your own player; it never streams "
            f"anything itself[/]",
            id="panel-title",
        )
        with VerticalScroll(id="panel-scroll"):
            yield Static("", id="music-now")
            yield Static(f"[{theme.ACCENT}][b]Sources[/b][/]", classes="section-title")
            yield Static(
                f"[{theme.TEXT_DIM}]Switching source pauses the player OmaPoker "
                f"started. A player you started yourself is left alone.[/]"
            )
            yield Horizontal(id="music-sources")
            yield Static(f"[{theme.ACCENT}][b]Transport[/b][/]", classes="section-title")
            with Horizontal(id="music-controls"):
                yield Button("Play / pause", id="music-toggle", variant="primary")
                yield Button("Previous", id="music-prev")
                yield Button("Next", id="music-next")
                yield Button("Volume -", id="music-vol-down")
                yield Button("Volume +", id="music-vol-up")
                yield Button("Shuffle", id="music-shuffle")
                yield Button("Repeat one", id="music-repeat")
            yield Static("", id="music-status")

            yield Static(
                f"[{theme.ACCENT}][b]Local library[/b][/]  "
                f"[{theme.TEXT_DIM}]played by OmaPoker's own mpv instance[/]",
                classes="section-title",
            )
            with Horizontal(id="music-library-controls"):
                yield Input(placeholder="search your music", id="music-search")
                yield Button("Scan folders", id="music-scan")
                yield Button("Play", id="music-play", variant="success")
                yield Button("Queue", id="music-queue")
                yield Button("Add folder", id="music-add-folder")
            yield Input(placeholder="folder to add, e.g. ~/Music/Albums", id="music-folder-input")
            yield Static("", id="music-folders")
            yield ListView(id="music-tracks")

            yield Static(f"[{theme.ACCENT}][b]Visualiser[/b][/]", classes="section-title")
            yield Horizontal(
                Switch(value=bool(self.app.settings.get("visualiser", True)),
                       id="music-visualiser"),
                Label(f"  cava equaliser in the header "
                      f"({'installed' if _cava_present() else 'cava not installed'})"),
            )
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_sources()
        self.refresh_now()
        self.refresh_folders()
        self.refresh_tracks()

    def on_unmount(self) -> None:
        self.app.save_music_settings()

    # -- painting ------------------------------------------------------
    def refresh_sources(self) -> None:
        row = self.query_one("#music-sources", Horizontal)
        row.remove_children()
        current = self.app.music.source
        widgets = []
        for source, label in self.app.music.source_labels():
            marker = "\u25cf " if source == current else ""
            widgets.append(
                Button(
                    f"{marker}{label}",
                    id=f"source-{_slug(source)}",
                    variant="primary" if source == current else "default",
                )
            )
        row.mount(*widgets)

    def refresh_now(self) -> None:
        state = self.app.music.refresh()
        lines = [f"[{theme.ACCENT}][b]{state.source_label}[/b][/]"]
        if state.source == OFF:
            lines.append(
                f"[{theme.TEXT_DIM}]Music is off. Pick a source above, or press C to "
                f"cycle through what is available.[/]"
            )
        else:
            lines.append(f"[{theme.TEXT}]{state.label}[/]")
            detail = []
            if state.duration:
                detail.append(f"{state.position:.0f}s / {state.duration:.0f}s")
            if state.queue_length:
                detail.append(f"track {state.queue_position}/{state.queue_length}")
            if state.shuffle:
                detail.append("shuffled")
            if detail:
                lines.append(f"[{theme.TEXT_DIM}]{'  ·  '.join(detail)}[/]")
        self.query_one("#music-now", Static).update("\n".join(lines))
        self.query_one("#music-status", Static).update(
            f"[{theme.TEXT_DIM}]{self.app.music.status_line()}[/]"
        )

    def refresh_folders(self) -> None:
        folders = self.app.music.library.folders
        if not folders:
            text = "[%s]No folders yet. Add one below to play your own files.[/]" % theme.TEXT_DIM
        else:
            text = "\n".join(f"[{theme.TEXT_DIM}]· {folder}[/]" for folder in folders)
            scanned = self.app.music.library.count() if self.app.music.library.scanned else 0
            text += f"\n[{theme.TEXT}]{scanned} tracks indexed[/]"
        self.query_one("#music-folders", Static).update(text)

    def refresh_tracks(self, term: str = "") -> None:
        library = self.app.music.library
        view = self.query_one("#music-tracks", ListView)
        view.clear()
        if not library.scanned and not library.folders:
            self._tracks = []
            return
        self._tracks = library.search(term, limit=200)
        for index, track in enumerate(self._tracks):
            view.append(
                ListItem(
                    Static(
                        f"[{theme.TEXT}]{track.title or track.path.stem}[/]"
                        f"[{theme.TEXT_DIM}]"
                        f"{('  -  ' + track.artist) if track.artist else ''}"
                        f"  ({track.path.suffix.lstrip('.')})[/]"
                    ),
                    id=f"track-{index}",
                )
            )
        if self._tracks:
            view.index = 0
            view.focus()

    # -- events --------------------------------------------------------
    def on_button_pressed(self, event: Button.Pressed) -> None:
        identifier = event.button.id or ""
        music = self.app.music
        if identifier.startswith("source-"):
            for source, _label in music.source_labels():
                if _slug(source) == identifier[len("source-"):]:
                    music.set_source(source)
                    break
            self.refresh_sources()
            self.refresh_now()
        elif identifier == "music-toggle":
            self.action_toggle()
        elif identifier == "music-prev":
            self.action_previous()
        elif identifier == "music-next":
            self.action_next()
        elif identifier == "music-vol-down":
            self.action_volume(-5)
        elif identifier == "music-vol-up":
            self.action_volume(5)
        elif identifier == "music-shuffle":
            self.action_shuffle()
        elif identifier == "music-repeat":
            music.set_repeat(not music.repeat)
            self.app.notify(f"Repeat one: {'on' if music.repeat else 'off'}")
            self.refresh_now()
        elif identifier == "music-scan":
            self.scan()
        elif identifier == "music-play":
            self.play_selected(queue=False)
        elif identifier == "music-queue":
            self.play_selected(queue=True)
        elif identifier == "music-add-folder":
            self.add_folder()

    def on_switch_changed(self, event: Switch.Changed) -> None:
        if event.switch.id == "music-visualiser":
            self.app.settings["visualiser"] = bool(event.value)
            self.app.store.save_settings(self.app.settings)
            self.app.notify(
                f"Visualiser {'on' if event.value else 'off'}. "
                f"It takes effect when you next open a table."
            )

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "music-search":
            self.refresh_tracks(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "music-folder-input":
            self.add_folder(event.value)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "music-tracks":
            self.play_selected(queue=False)

    # -- actions -------------------------------------------------------
    def action_toggle(self) -> None:
        self.app.music.toggle_play()
        self.refresh_now()

    def action_next(self) -> None:
        self.app.music.next_track()
        self.refresh_now()

    def action_previous(self) -> None:
        self.app.music.previous_track()
        self.refresh_now()

    def action_cycle(self) -> None:
        self.app.music.cycle_source()
        self.refresh_sources()
        self.refresh_now()

    def action_volume(self, delta: int) -> None:
        level = self.app.music.adjust_volume(delta)
        self.app.notify(f"Volume {level}%")
        self.refresh_now()

    def action_shuffle(self) -> None:
        enabled = self.app.music.set_shuffle(not self.app.music.shuffle)
        self.app.notify(f"Shuffle {'on' if enabled else 'off'}")
        self.refresh_now()

    # -- library -------------------------------------------------------
    def scan(self) -> None:
        if not self.app.music.library.folders:
            self.app.notify(
                "Add a folder first - OmaPoker will not go rummaging through your "
                "home directory on its own.",
                severity="warning",
            )
            return
        self.app.notify("Scanning your music folders...")
        self.call_after_refresh(self._scan_and_refresh)

    def _scan_and_refresh(self) -> None:
        count = self.app.music.rescan()
        self.app.notify(f"Indexed {count} tracks.")
        self.refresh_folders()
        self.refresh_tracks()

    def add_folder(self, value: str = "") -> None:
        box = self.query_one("#music-folder-input", Input)
        raw = (value or box.value).strip()
        if not raw:
            return
        from pathlib import Path

        folder = Path(raw).expanduser()
        if not folder.is_dir():
            self.app.notify(f"{folder} is not a folder.", severity="error")
            return
        folders = list(self.app.music.library.folders)
        if folder not in folders:
            folders.append(folder)
        self.app.music.library.set_folders(folders)
        box.value = ""
        self.app.save_music_settings()
        self.app.notify(f"Added {folder}.")
        self.refresh_folders()

    def play_selected(self, queue: bool = False) -> None:
        view = self.query_one("#music-tracks", ListView)
        index = view.index
        if index is None or not 0 <= index < len(self._tracks):
            self.app.notify("Pick a track first.", severity="warning")
            return
        track = self._tracks[index]
        music = self.app.music
        if queue:
            ok = music.enqueue_local([track])
            verb = "queued"
        else:
            ok = music.play_local([track])
            verb = "playing"
        if ok:
            self.app.notify(f"{verb.title()}: {track.label}")
            self.refresh_sources()
        else:
            self.app.notify(
                "mpv would not start. Install mpv, or use an external player.",
                severity="error",
            )
        self.refresh_now()


def _cava_present() -> bool:
    import shutil

    return shutil.which("cava") is not None


def _slug(source: str) -> str:
    return source.replace(":", "-").replace(".", "_").replace("/", "_")


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------

class SettingsScreen(BaseScreen):
    """Everything that is not a poker decision."""

    def compose(self) -> ComposeResult:
        player = self.app.player
        profile = player.profile
        settings = self.app.settings

        yield Static(f"[{theme.ACCENT}][b]Settings[/b][/]", id="panel-title")
        with VerticalScroll(id="panel-scroll"):
            yield Static(f"[{theme.ACCENT}][b]Profile[/b][/]", classes="section-title")
            yield Input(value=profile.name, id="settings-name")
            yield Select(
                [(EXPERIENCE_LABELS[level], level) for level in EXPERIENCE_LEVELS],
                value=profile.experience,
                allow_blank=False,
                id="settings-experience",
            )
            yield Static(
                f"[{theme.TEXT_DIM}]Profile {profile.id} · created "
                f"{profile.created or 'unknown'}[/]"
            )
            yield Static(
                f"[{theme.TEXT}]Identity[/]  [{theme.ACCENT}]{profile.identity.code}[/]  "
                + (
                    f"[{theme.ACCENT}]verifiable[/]"
                    if crypto_available() and profile.identity.private_hex
                    else f"[{theme.WARN}]unverified label only (no key material)[/]"
                )
            )
            yield Button("Save profile", id="settings-save", variant="success")

            yield Static(f"[{theme.ACCENT}][b]Assist dial[/b][/]", classes="section-title")
            yield Static("", id="settings-dial-text")
            yield Horizontal(id="dial-row")
            for level in range(MIN_LEVEL, MAX_LEVEL + 1):
                yield Button(f"{level}", id=f"dial-{level}")

            yield Static(f"[{theme.ACCENT}][b]Table[/b][/]", classes="section-title")
            yield Select(
                [("Unicode suits (A♠)", "glyphs"), ("Plain ASCII (As)", "ascii")],
                value=profile.cards_style if profile.cards_style in ("glyphs", "ascii") else "glyphs",
                allow_blank=False,
                id="settings-cards",
            )
            yield Horizontal(
                Switch(value=bool(profile.sound), id="settings-sound"),
                Label("  terminal bell when it is your turn"),
            )

            yield Static(f"[{theme.ACCENT}][b]Bankroll[/b][/]", classes="section-title")
            yield Static(
                f"[{theme.TEXT}]{player.bankroll.chips:,}[/] "
                f"[{theme.TEXT_DIM}]chips · {len(player.bankroll.ledger)} ledger entries[/]"
            )
            yield Button(
                f"Reset bankroll to {STARTING_CHIPS:,} chips", id="settings-reset"
            )

            yield Static(f"[{theme.ACCENT}][b]Networking[/b][/]", classes="section-title")
            yield Input(
                value=str(settings.get("host_name", "OmaPoker table")),
                placeholder="name shown in the lobby",
                id="settings-host-name",
            )
            yield Input(
                value=str(settings.get("lan_port", 8760)),
                placeholder="port",
                id="settings-port",
            )
            yield Static(
                f"[{theme.TEXT_DIM}]LAN games use this port. The host machine runs the "
                f"table; everyone else joins with the room code.[/]"
            )

            yield Static(f"[{theme.ACCENT}][b]Content[/b][/]", classes="section-title")
            yield Static(
                f"[{theme.TEXT_DIM}]lessons and glossary: {self.app.content.source}\n"
                f"config: {self.app.store.root}\n"
                f"Put your own lessons.json / glossary.json / charts.json in "
                f"{self.app.store.root / 'content'} to override the bundled ones.[/]"
            )

            yield Static(f"[{theme.ACCENT}][b]Danger zone[/b][/]", classes="section-title")
            yield Button("Delete this profile", id="settings-delete", variant="error")
            yield Button("Back", id="settings-back")
        yield Footer()

    def on_mount(self) -> None:
        self.show_dial()

    def show_dial(self) -> None:
        dial = self.app.dial
        self.query_one("#settings-dial-text", Static).update(
            f"{theme.assist_dots(dial.level)}  [{theme.TEXT}]{dial.level}/5 - "
            f"{dial.title}[/]\n[{theme.TEXT_DIM}]{dial.summary}[/]"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        identifier = event.button.id or ""
        if identifier.startswith("dial-"):
            self.app.set_assist(int(identifier.split("-", 1)[1]))
            self.show_dial()
        elif identifier == "settings-save":
            self.save_profile()
        elif identifier == "settings-reset":
            self.app.push_screen(
                ConfirmScreen(
                    f"Reset your bankroll to {STARTING_CHIPS:,} chips?",
                    detail="Useful if a losing streak has left you unable to buy in "
                    "anywhere. The ledger keeps the record.",
                ),
                self._reset_done,
            )
        elif identifier == "settings-delete":
            self.delete_profile()
        elif identifier == "settings-back":
            self.action_back()

    def on_select_changed(self, event: Select.Changed) -> None:
        value = event.value
        if event.select.id == "settings-cards" and isinstance(value, str):
            self.app.player.profile.cards_style = value
            self.app.save_profile()
            self.app.notify("Card style saved.")
        elif event.select.id == "settings-experience" and isinstance(value, str):
            profile = self.app.player.profile
            profile.experience = value
            self.app.set_assist(self.app.dial.level)
            self.app.save_profile()
            self.app.notify(f"Experience set to {SOURCE_LABELS.get(value, value)}.")

    def on_switch_changed(self, event: Switch.Changed) -> None:
        if event.switch.id == "settings-sound":
            self.app.player.profile.sound = bool(event.value)
            self.app.save_profile()

    def save_profile(self) -> None:
        name = self.query_one("#settings-name", Input).value.strip()
        if name:
            self.app.player.profile.name = name
        host_name = self.query_one("#settings-host-name", Input).value.strip()
        port_raw = self.query_one("#settings-port", Input).value.strip()
        self.app.settings["host_name"] = host_name or "OmaPoker table"
        if port_raw.isdigit() and 1024 <= int(port_raw) <= 65535:
            self.app.settings["lan_port"] = int(port_raw)
        else:
            self.app.notify("The port must be between 1024 and 65535; keeping the old one.",
                            severity="warning")
        self.app.store.save_settings(self.app.settings)
        self.app.save_profile()
        self.app.notify("Settings saved.")

    def _reset_done(self, ok: bool | None) -> None:
        if ok:
            self.app.reset_bankroll()
            self.app.notify("Bankroll reset.")

    def delete_profile(self) -> None:
        profile = self.app.player.profile
        self.app.push_screen(
            ConfirmScreen(
                f"Delete {profile.name}?",
                detail="Bankroll, statistics and identity go with it. Timestamped "
                "backups stay in the config folder.",
                danger=True,
            ),
            self._delete_done,
        )

    def _delete_done(self, ok: bool | None) -> None:
        if not ok:
            return
        self.app.delete_active_profile()


# --------------------------------------------------------------------------
# review
# --------------------------------------------------------------------------

class ReviewScreen(PanelScreen):
    """The last hand, explained."""

    title = "Hand review"

    def __init__(self, outcome, session, seat_index: int) -> None:
        super().__init__()
        self._outcome = outcome
        self._session = session
        self._seat_index = seat_index

    def body(self) -> ComposeResult:
        outcome = self._outcome
        session = self._session
        board = " ".join(theme.card_markup(c) for c in outcome.board)
        yield Static(f"Board: {board}" if board else "[%s]No board was dealt.[/]" % theme.TEXT_DIM)
        seat = session.seats[self._seat_index] if self._seat_index < len(session.seats) else None
        if seat is not None:
            net = outcome.net.get(self._seat_index, 0)
            yield Static(
                f"[{theme.TEXT}]{seat.name}[/] finished {theme.result_markup(net)} "
                f"[{theme.TEXT_DIM}](stack {seat.stack:,})[/]"
            )
        for index, hole in sorted(outcome.reveal.items()):
            cards = " ".join(theme.card_markup(c) for c in hole)
            yield Static(f"[{theme.TEXT_DIM}]{session.seats[index].name} showed[/] {cards}")
        if not outcome.reveal:
            yield Static(f"[{theme.TEXT_DIM}]Nobody showed: the hand ended on a fold.[/]")
        yield Static("")
        for line in review_hand(outcome, session.seats, self._seat_index, self.app.dial):
            yield Static(f"[{theme.BLUE}]•[/] {line}")
        if not self.app.dial.shows_review:
            yield Static(
                f"[{theme.TEXT_DIM}]Turn the assist dial up to 5 for the full "
                f"step-by-step review.[/]"
            )


__all__ = [
    "AssistScreen",
    "GlossaryScreen",
    "HelpScreen",
    "LessonsScreen",
    "MessageScreen",
    "MusicScreen",
    "ReviewScreen",
    "SettingsScreen",
    "StatsScreen",
]
