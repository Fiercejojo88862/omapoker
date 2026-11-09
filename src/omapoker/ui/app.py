"""The application shell.

Everything the screens need - the profile bundle, the assist dial, the music
controller, the live session and the screen navigator - hangs off this object.
Screens never reach into storage or the network themselves; they ask the app.

That is what makes the table screen testable: a test can build an app around a
throwaway config directory, drive the table with keypresses, and assert on the
session's state afterwards.
"""

from __future__ import annotations

import random
import zlib

from textual.app import App
from textual.binding import Binding

from .. import paths
from ..coach import AssistDial, load_content
from ..data import DataStore, Profile, STARTING_CHIPS, PlayerData, Bankroll, Stats
from ..engine.game import Street
from ..music import MusicController
from ..session import GameSession, Mode
from .screens.base import ConfirmScreen
from .screens.menu import MainMenuScreen
from .screens.panels import (
    GlossaryScreen,
    HelpScreen,
    LessonsScreen,
    MusicScreen,
    SettingsScreen,
    StatsScreen,
)


class OmaPokerApp(App):
    """OmaPoker itself."""

    CSS_PATH = "styles.tcss"
    TITLE = "OmaPoker"
    SUB_TITLE = "Texas Hold'em for Omarchy"
    BINDINGS = [
        Binding("ctrl+q", "request_quit", "Quit", priority=True),
        Binding("f1", "help", "Help"),
    ]

    def __init__(
        self,
        *,
        store: DataStore | None = None,
        seed: int | None = None,
    ) -> None:
        super().__init__()
        self.store = store
        self.rng = random.Random(seed)
        self.settings: dict = {}
        self.player: PlayerData | None = None
        self.dial: AssistDial = AssistDial()
        self.music: MusicController | None = None
        self.content = None
        self.session: GameSession | None = None
        #: The listening socket of a hosted LAN table, if one is open.
        self.host_server = None

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def on_mount(self) -> None:
        paths.ensure_dirs()
        if self.store is None:
            self.store = DataStore()
        self.settings = self.store.settings()
        self.settings.setdefault("visualiser", True)
        self.content = load_content(str(paths.content_dir()))
        self.music = MusicController(self.music_settings(), state_dir=paths.STATE_DIR)

        profile = self.store.active_profile()
        if profile is None:
            from .screens.onboarding import OnboardingScreen

            self.push_screen(OnboardingScreen())
            return
        self.load_profile(profile)
        self.open_menu()

    def on_unmount(self) -> None:
        # However the app is leaving - the quit key, Ctrl+C, or an exit from a
        # screen - the stack goes back on the bankroll. A hard kill never gets
        # here; that is what the sitting note on disk is for.
        self.cash_in_on_exit()
        self.close_hosting()
        self.save_music_settings()
        if self.music is not None and self.music.visualiser is not None:
            self.music.visualiser.stop()

    def cash_in_on_exit(self) -> None:
        """Fold out and cash in, unless that has already happened."""
        if self.session is None:
            return
        try:
            self._fold_out_and_cash_in()
        except Exception:  # pragma: no cover - never block a shutdown
            # The books are already safe: the sitting note stays on disk and the
            # next start recovers it.
            return
        self.session = None

    # ------------------------------------------------------------------
    # profiles and settings
    # ------------------------------------------------------------------
    def music_settings(self) -> dict:
        settings = dict(self.settings or {})
        profile = self.player.profile if self.player else None
        if profile is not None:
            settings.update(
                {
                    "music_source": profile.music_source,
                    "music_volume": profile.music_volume,
                    "music_folders": list(profile.music_folders),
                    "music_resume": profile.music_resume,
                }
            )
        return settings

    def save_music_settings(self) -> None:
        if self.music is None:
            return
        updates = self.music.settings_updates()
        if self.player is not None:
            profile = self.player.profile
            profile.music_source = str(updates.get("music_source", "off"))
            profile.music_volume = int(updates.get("music_volume", 60))
            profile.music_folders = [str(f) for f in updates.get("music_folders", [])]
            profile.music_resume = bool(updates.get("music_resume", False))
            self.store.save_profile(profile)
        self.settings["visualiser"] = bool(updates.get("visualiser", True))
        self.settings["visualiser_bars"] = int(updates.get("visualiser_bars", 12))
        self.store.save_settings(self.settings)

    def load_profile(self, profile: Profile) -> None:
        self.player = self.store.player(profile.id)
        if self.player is None:
            self.player = PlayerData(
                profile=profile,
                bankroll=self.store.bankroll_for(profile.id),
                stats=self.store.stats_for(profile.id),
            )
        self.dial = AssistDial(int(profile.assist_level))
        self.store.set_active_profile(profile.id)
        self.recover_sitting()

    def recover_sitting(self) -> None:
        """Put back a stack left behind by an unclean exit, if there is one."""
        if self.player is None or self.store is None:
            return
        recovered = self.store.recover_table_seat(self.player)
        if recovered is None:
            return
        chips, level = recovered
        self.notify(
            f"Recovered {chips:,} chips from {level}: the app did not get to "
            f"cash them in.",
            severity="warning",
        )

    def create_profile(self, name: str, experience: str) -> Profile:
        profile = self.store.create_profile(name, experience)
        self.load_profile(profile)
        if self.music is not None:
            self.music.library.set_folders(profile.music_folders)
        return profile

    def switch_profile(self, profile_id: str) -> None:
        profile = self.store.load_profile(profile_id)
        if profile is None:
            self.notify("That profile is gone.", severity="error")
            return
        self.save_music_settings()
        self.load_profile(profile)
        if self.music is not None:
            self.music.library.set_folders(profile.music_folders)
            self.music.volume = int(profile.music_volume)
            self.music.source = str(profile.music_source or "off")
        self.notify(f"Now playing as {profile.name}.")
        self.open_menu()

    def save_profile(self) -> None:
        if self.player is not None:
            self.player.profile.assist_level = self.dial.level
            self.store.save_player(self.player)

    def set_assist(self, level: int) -> None:
        self.dial = self.dial.with_level(int(level))
        if self.player is not None:
            self.player.profile.assist_level = self.dial.level
            self.store.save_profile(self.player.profile)
        self.notify(f"Assist {self.dial.level}/5 - {self.dial.title}")

    def reset_bankroll(self) -> None:
        if self.player is None:
            return
        bankroll = self.player.bankroll
        delta = STARTING_CHIPS - bankroll.chips
        bankroll.chips = STARTING_CHIPS
        bankroll._record("reset", delta, "bankroll reset")
        self.store.bankroll_store.save_bankroll(bankroll)
        # A reset settles the books: an old sitting note must not top it up again.
        self.store.clear_table_seat(self.player.profile.id)

    def delete_active_profile(self) -> None:
        if self.player is None:
            return
        identifier = self.player.profile.id
        self.store.delete_profile(identifier)
        self.session = None
        self.player = None
        profile = self.store.active_profile()
        if profile is None:
            from .screens.onboarding import OnboardingScreen

            self.switch_screen(OnboardingScreen())
            return
        self.load_profile(profile)
        self.open_menu()

    # ------------------------------------------------------------------
    # navigation
    # ------------------------------------------------------------------
    def open_menu(self) -> None:
        # A finished table also retires its picker and lobby. Leaving them
        # underneath would keep their timers and old connections alive.
        while len(self.screen_stack) > 2:
            self.pop_screen()
        if len(self.screen_stack) == 1:
            self.push_screen(MainMenuScreen())
        else:
            self.switch_screen(MainMenuScreen())

    def open_ladder(self) -> None:
        from .screens.ladder import TablePickerScreen

        self.push_screen(TablePickerScreen(Mode.SOLO))

    def open_hotseat_setup(self) -> None:
        from .screens.ladder import HotSeatSetupScreen

        self.push_screen(HotSeatSetupScreen())

    def open_table(self) -> None:
        session = self.session
        if (
            session is not None
            and session.mode is Mode.HOST
            and self.host_server is not None
        ):
            from .screens.net import HostTableScreen

            self.push_screen(HostTableScreen(self.host_server))
            return
        from .screens.table import TableScreen

        self.push_screen(TableScreen())

    def open_stats(self) -> None:
        self.push_screen(StatsScreen())

    def open_lessons(self) -> None:
        self.push_screen(LessonsScreen())

    def open_settings(self) -> None:
        self.push_screen(SettingsScreen())

    def open_glossary(self) -> None:
        self.push_screen(GlossaryScreen())

    def open_help(self) -> None:
        self.push_screen(HelpScreen())

    def open_music(self) -> None:
        if self.music is None:
            self.notify("Music is not available in this session.", severity="warning")
            return
        self.push_screen(MusicScreen())

    def open_host(self) -> None:
        from .screens.net import HostScreen

        self.push_screen(HostScreen())

    def open_join(self) -> None:
        from .screens.net import JoinScreen

        self.push_screen(JoinScreen())

    def action_help(self) -> None:
        self.open_help()

    # ------------------------------------------------------------------
    # sessions
    # ------------------------------------------------------------------
    def guest_players(self, names: list[str], buy_in: int) -> list[PlayerData]:
        """Throwaway players for hot-seat friends: never written to disk.

        A hosted LAN table uses the same trick for its remote players: their
        stack is created by the host, so it has to be a guest (or the host's own
        bankroll would be paying for everybody else's chairs).
        """
        guests = [self.player]
        for position, name in enumerate(names[1:], start=1):
            slug = f"guest-{position}-{zlib.crc32(name.encode()) % 10_000:04d}"
            profile = Profile.new(slug, name, "casual")
            bankroll = Bankroll(profile_id=slug, chips=buy_in)
            bankroll._record("start", buy_in, "hot-seat guest stack")
            guests.append(
                PlayerData(
                    profile=profile,
                    bankroll=bankroll,
                    stats=Stats(profile_id=slug),
                    guest=True,
                )
            )
        return guests

    def start_session(
        self,
        level,
        *,
        mode: Mode = Mode.SOLO,
        human_names: list[str] | None = None,
        bot_level: str = "medium",
        bot_count: int = 4,
    ) -> GameSession | None:
        if self.player is None:
            self.notify("No profile is loaded.", severity="error")
            return None
        names = human_names or [self.player.profile.name]
        if mode in (Mode.HOTSEAT, Mode.HOST):
            # Hot-seat friends and remote players both bring their own chair at
            # the host's expense: throwaway stacks that are never persisted.
            players = self.guest_players(names, level.buy_in)
        else:
            players = [self.player]
        self.save_music_settings()
        seed = self.rng.getrandbits(64)
        self.session = GameSession(
            self.player,
            level,
            store=self.store,
            bot_level=bot_level,
            bot_count=bot_count,
            human_names=names,
            human_players=players,
            seed=seed,
            mode=mode,
        )
        self.open_table()
        return self.session

    def leave_table(self) -> None:
        """Fold out, cash in, and go back to the menu."""
        self.close_hosting()
        if self.session is None:
            self.pop_screen()
            return
        returned = self._fold_out_and_cash_in()
        table = self.session.level.name
        self.session = None
        self.notify(f"Left the {table} table with {returned:,} chips.")
        self.open_menu()

    def close_hosting(self) -> None:
        """Stop listening, if we were hosting a LAN table."""
        server = self.host_server
        self.host_server = None
        if server is not None:
            try:
                server.stop()
            except Exception:  # pragma: no cover - never block leaving a table
                pass

    def host_disconnected(self, *, from_client: bool = False) -> None:
        """The LAN link went away: the joiner goes home, the host closes up."""
        self.close_hosting()
        if self.session is not None:
            if from_client:
                # A guest holds no bankroll here - their stack was the host's
                # to create, so there is nothing to cash back in.
                self.session = None
            else:
                self._fold_out_and_cash_in()
                self.session = None
        self.notify(
            "You left the table." if from_client else "The host closed the table.",
            severity="warning",
        )
        self.open_menu()

    def _fold_out_and_cash_in(self) -> int:
        session = self.session
        if session is None:
            return 0
        hand = session.hand
        guard = 0
        while (
            hand is not None
            and hand.street is not Street.COMPLETE
            and guard < 400
        ):
            guard += 1
            actor = session.actor()
            if actor is None:
                break
            if actor.is_human:
                legal = session.legal_actions()
                if legal.can_fold:
                    session.human_action("fold")
                elif legal.can_check:
                    session.human_action("check")
                elif legal.can_call:
                    session.human_action("call", legal.call_amount)
                else:
                    break
            else:
                session.run_bots()
        session.finish_hand()
        return session.leave()

    # ------------------------------------------------------------------
    # notifications
    # ------------------------------------------------------------------
    def notify_playing(self) -> None:
        if self.music is None:
            return
        state = self.music.state()
        self.notify(state.label if state.title else state.source_label)

    def request_quit(self) -> None:
        session = self.session
        live = (
            session is not None
            and session.hand is not None
            and session.hand.street is not Street.COMPLETE
        )
        if not live:
            self._quit_confirmed(True)
            return
        self.push_screen(
            ConfirmScreen(
                "Quit OmaPoker with a hand in progress?",
                detail="Your cards are folded and your chips come back to your "
                "bankroll.",
            ),
            self._quit_confirmed,
        )

    def action_request_quit(self) -> None:
        self.request_quit()

    def _quit_confirmed(self, ok: bool | None) -> None:
        if not ok:
            return
        self._fold_out_and_cash_in()
        self.close_hosting()
        self.session = None
        self.exit()


__all__ = ["OmaPokerApp"]
