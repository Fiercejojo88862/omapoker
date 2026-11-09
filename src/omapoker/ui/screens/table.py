"""The poker table.

Layout, from the top: a status bar with the music chip in the corner, the seat
ring with the board and pot in the middle, the coach panel and the hand log, and
finally the action bar. Everything above the action bar is read-only; every way
of playing the hand lives in the action bar or in a single keypress.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, Static

from ...coach.advice import build_advice, review_hand
from ...engine import cards as C
from ...engine.game import IllegalAction, LegalActions, Street
from ...music import idle_wave
from .. import theme
from ..widgets import (
    ActionBar,
    BoardPanel,
    CoachPanel,
    Room,
    LogPanel,
    MusicChip,
    PokerTable,
    SeatCard,
)
from .base import BaseScreen, ChoiceScreen, ConfirmScreen, PrivacyScreen

BOARD = object()  # sentinel for the board cell in the middle of the ring

#: Clockwise seat placement, starting at the hero's own seat at the bottom.
CLOCK = [
    "bottom-center",
    "bottom-right",
    "mid-right",
    "top-right",
    "top-center",
    "top-left",
    "mid-left",
    "bottom-left",
]

def ring_rows(count: int, hero_index: int = 0) -> list[list]:
    """Seats arranged around the board, clock-wise from the bottom centre.

    Returned as rows of cells: an int is a seat index, :data:`BOARD` is the
    community cards. Empty rows are dropped; empty cells inside a row are kept
    as spacers so short-handed opponents still sit across from one another.
    """
    slots: dict[str, int | None] = {name: None for name in CLOCK}
    extra: list[int] = []
    clockwise = [((hero_index + offset) % count) for offset in range(count)] if count else []
    for offset, index in enumerate(clockwise):
        if offset < len(CLOCK):
            # Spread short-handed tables around the full ring. In particular,
            # heads-up players sit opposite one another instead of side by side.
            slot_index = (offset * len(CLOCK)) // min(count, len(CLOCK))
            slots[CLOCK[slot_index]] = index
        else:
            extra.append(index)
    rows = [
        [slots["top-left"], slots["top-center"], slots["top-right"]],
        [slots["mid-left"], BOARD, slots["mid-right"]],
        [slots["bottom-left"], slots["bottom-center"], slots["bottom-right"]],
    ]
    if extra:
        rows.append(extra)
    return [row for row in rows if any(cell is not None for cell in row)]


LOG_KINDS = {
    "fold": "info",
    "check": "action",
    "call": "action",
    "bet": "action",
    "raise": "action",
    "blind": "info",
    "all_in": "all_in",
    "incomplete_raise": "info",
    "all_in_runout": "info",
    "street": "street",
    "win_uncontested": "win",
    "pot_award": "win",
    "showdown": "win",
    "hand_start": "info",
    "ante": "info",
}


class TableScreen(BaseScreen):
    """One table, played hand after hand."""

    BINDINGS = [
        Binding("f", "fold", "Fold"),
        Binding("c", "check_call", "Check/Call"),
        Binding("r", "raise_mode", "Raise"),
        Binding("b", "bet_mode", "Bet"),
        Binding("x", "all_in", "All in", show=False),
        Binding("n", "next_hand", "Next hand"),
        Binding("a", "assist_menu", "Assist"),
        Binding("v", "review", "Review", show=False),
        Binding("l", "toggle_log", "Log", show=False),
        Binding("q", "leave_table", "Leave table"),
        Binding("escape", "cancel_or_leave", "Cancel", show=False),
        Binding("enter", "confirm_amount", "Confirm", show=False),
        Binding("space", "music_toggle", "Play/pause", show=False),
        Binding("comma", "volume_down", "Vol -", show=False),
        Binding("full_stop", "volume_up", "Vol +", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._advice = None
        self._advice_key: tuple | None = None
        self._hand_over = False
        self._outcome = None
        self._viewer: int | None = None
        self._privacy_showing = False
        self._logged_hand = None
        self._logged_count = 0
        self._pending = ""
        #: None means "every human seat is mine" (offline play). A hosted LAN
        #: table sets this to just the host's seat so remote turns are not
        #: offered to the wrong keyboard.
        self._my_seats: set[int] | None = None
        self._phase = 0.0
        self._levels: list[float] = [0.0] * 12
        self._live = False
        self._chip_text = "Off"

    # ------------------------------------------------------------------
    # layout
    # ------------------------------------------------------------------
    def _ring_rows(self, count: int) -> list[list]:
        # Keeping this helper callable without a mounted screen makes the ring
        # geometry easy to unit-test.
        hero_index = self._hero_index() if self is not None else 0
        return ring_rows(count, hero_index)

    def _hero_index(self) -> int:
        session = self.app.session
        if self._my_seats:
            return min(self._my_seats)
        if session is not None and session.human_seats:
            return session.human_seats[0]
        return 0

    def compose(self) -> ComposeResult:
        session = self.app.session
        ring: list[Horizontal] = []
        for row_number, row in enumerate(self._ring_rows(len(session.seats))):
            cells: list = []
            for cell in row:
                if cell is BOARD:
                    cells.append(PokerTable(id="poker-table"))
                elif isinstance(cell, int):
                    cells.append(SeatCard(cell, id=f"seat-{cell}"))
                else:
                    cells.append(Static("", classes="seat-spacer"))
            row_class = (
                "table-row"
                if BOARD in row
                else ("hero-row" if row_number == 2 else "outer-row")
            )
            ring.append(
                Horizontal(
                    Static("", classes="seat-fill"),
                    *cells,
                    Static("", classes="seat-fill"),
                    classes=f"ring-row {row_class}",
                )
            )

        yield Horizontal(
            Static("", id="topbar-left"),
            MusicChip(id="music-chip-wrap"),
            id="topbar",
        )
        yield Vertical(
            Room(Vertical(*ring, id="ring"), id="room"),
            Horizontal(
                CoachPanel("", id="coach-panel"),
                LogPanel(id="log-panel"),
                id="bottom-rows",
            ),
            id="table-body",
        )
        yield ActionBar(id="actionbar")

    def on_mount(self) -> None:
        self.set_interval(1.5, self._tick_music)
        self.set_interval(0.15, self._tick_bars)
        if self.app.music.visualiser is not None:
            self.app.music.visualiser.start()
        self._tick_music()
        self.call_after_refresh(self._begin)

    def on_unmount(self) -> None:
        if self.app.music.visualiser is not None:
            self.app.music.visualiser.stop()

    # ------------------------------------------------------------------
    # whose turn is it
    # ------------------------------------------------------------------
    def _may_act(self) -> bool:
        """True when this keyboard is allowed to act right now."""
        if self._privacy_showing:
            return False
        session = self.app.session
        if session is None or not session.awaiting_human:
            return False
        actor = session.active_human
        if actor is None:
            return False
        return self._my_seats is None or actor.index in self._my_seats

    def _broadcast(self) -> None:
        """Hook for the hosted table: nothing to do offline."""

    # ------------------------------------------------------------------
    # hand flow
    # ------------------------------------------------------------------
    def _begin(self) -> None:
        session = self.app.session
        if session is None:
            return
        if session.hand is None or session.hand.street is Street.COMPLETE:
            if session.live:
                session.start_hand()
                self._hand_over = False
                self._outcome = None
                self._logged_hand = None
                self._advice_key = None
        self._progress()

    def _progress(self) -> None:
        """Run the bots, close a finished hand, then repaint."""
        session = self.app.session
        if session is None or session.hand is None:
            return
        if session.hand.street is not Street.COMPLETE:
            session.run_bots()
        self._log_new_events()
        if session.hand.street is Street.COMPLETE:
            if not self._hand_over:
                self._finish_hand()
        else:
            self._maybe_privacy()
        self.refresh_table()
        self._broadcast()

    def _finish_hand(self) -> None:
        session = self.app.session
        outcome = session.finish_hand()
        self._hand_over = True
        self._outcome = outcome
        self._viewer = None
        if outcome:
            for index in session.human_seats:
                net = outcome.net.get(index, 0)
                seat = session.seats[index]
                if net > 0:
                    self._log(f"{seat.name} finishes the hand +{net:,}", "win")
                elif net < 0:
                    self._log(f"{seat.name} finishes the hand {net:,}", "info")
        self.call_after_refresh(self._handle_busts)

    def _maybe_privacy(self) -> None:
        if self._my_seats is not None:
            # A hosted table: everybody has their own screen, so there is
            # nothing to cover up - and the host must never be shown a remote
            # player's cards.
            return
        session = self.app.session
        actor = session.active_human
        if actor is None:
            return
        if (
            len(session.human_seats) > 1
            and self._viewer != actor.index
            and not self._privacy_showing
        ):
            self._viewer = None
            self._privacy_showing = True
            self._log(f"Pass the keyboard to {actor.name}", "info")
            self.app.push_screen(PrivacyScreen(actor.name), self._privacy_done)

    def _privacy_done(self, _result) -> None:
        self._privacy_showing = False
        actor = self.app.session.active_human if self.app.session is not None else None
        self._viewer = actor.index if actor is not None else None
        self.refresh_table()

    def _log_new_events(self) -> None:
        session = self.app.session
        hand = session.hand if session else None
        if hand is None:
            return
        if hand is not self._logged_hand:
            self._logged_hand = hand
            self._logged_count = 0
        events = hand.events
        for event in events[self._logged_count :]:
            kind = LOG_KINDS.get(event.kind, "info")
            self._log(event.text, kind)
        self._logged_count = len(events)

    def _log(self, text: str, kind: str = "info") -> None:
        if not text:
            return
        try:
            self.query_one(LogPanel).add_event(text, kind)
        except Exception:  # pragma: no cover - log is cosmetic
            pass

    # ------------------------------------------------------------------
    # repaint
    # ------------------------------------------------------------------
    def refresh_table(self) -> None:
        session = self.app.session
        if session is None:
            return
        profile = self.app.player.profile
        style = theme.cards_style(profile.cards_style)
        viewer = self._viewer
        if viewer is None:
            if self._my_seats:
                # A hosted table: your own cards, whoever else is sitting here.
                viewer = sorted(self._my_seats)[0]
            elif len(session.human_seats) == 1:
                # Solo play: you always get to see your own cards, even once the
                # hand has finished. Hot-seat hides them until its cover screen.
                viewer = session.human_seats[0]
        state = session.state(viewer=viewer)
        board = [C.parse_card(text) for text in state.get("board", [])]

        self.query_one("#topbar-left", Static).update(self._top_text(session, state))

        for seat in session.seats:
            entry = state["seats"][seat.index]
            try:
                card = self.query_one(f"#seat-{seat.index}", SeatCard)
            except Exception:  # pragma: no cover - seat ring is fixed
                continue
            hole = [
                C.parse_card(text) for text in entry.get("hole", []) if isinstance(text, str)
            ]
            card.show(
                name=seat.name,
                stack=entry.get("stack", seat.stack),
                hole=hole,
                hidden=bool(entry.get("hole_hidden")),
                folded=bool(entry.get("folded")),
                all_in=bool(entry.get("all_in")),
                last_action=str(entry.get("last_action") or ""),
                dealer=state.get("button") == seat.index,
                your_turn=state.get("to_act") == seat.index,
                is_you=seat.index in session.human_seats,
                style=style,
                hand_label=self._seat_hand_label(session, seat.index, hole, board),
                sitting_out=bool(entry.get("sitting_out")),
                prominent=seat.index == self._hero_index(),
            )

        self.query_one(BoardPanel).show(
            board,
            state.get("pot", 0),
            state.get("street", "preflop"),
            style=style,
            current_bet=state.get("current_bet", 0),
        )
        self._refresh_actions()
        self._refresh_coach(session, style)

    def _seat_hand_label(self, session, seat_index: int, hole, board) -> str:
        """Show the made hand only for cards the viewer can already see."""
        if not self.app.dial.shows_hand_name or not hole:
            return ""
        if len(board) < 3:
            return ""
        from ...engine import evaluator as E

        return E.hand_name_short(E.evaluate(list(hole) + list(board)))

    def _top_text(self, session, state) -> str:
        level = session.level
        hero = session.seats[0]
        return (
            f"[{theme.ACCENT}]{level.name}[/]  "
            f"[{theme.TEXT_DIM}]hand[/] [{theme.TEXT}]#{session.hands_played}[/]  "
            f"[{theme.TEXT_DIM}]pot[/] [{theme.ACCENT}]{state.get('pot', 0):,}[/]\n"
            f"[{theme.TEXT_DIM}]bankroll[/] [{theme.TEXT}]"
            f"{self.app.player.bankroll.chips:,}[/]  "
            f"[{theme.TEXT_DIM}]table[/] [{theme.TEXT}]{hero.stack:,}[/]  "
            f"[{theme.TEXT_DIM}]assist[/] {theme.assist_dots(self.app.dial.level)}"
        )

    def _refresh_actions(self) -> None:
        session = self.app.session
        bar = self.query_one(ActionBar)
        if not self._may_act():
            if self._pending:
                self._pending = ""
                bar.clear_amount()
            bar.set_legal(LegalActions(can_fold=False), enabled=False, note=self._idle_note())
            return
        legal = session.legal_actions()
        note = ""
        if self._pending:
            note = (
                f"{self._pending.title()}: type an amount and press Enter "
                f"(Esc cancels)"
            )
        bar.set_legal(legal, enabled=True, note=note)
        if not self._pending and not bar.amounts():
            bar.suggest(self._default_amount(legal))

    def _idle_note(self) -> str:
        if self._hand_over:
            return "Hand complete. Press N for the next hand."
        session = self.app.session
        if session is not None and session.awaiting_human:
            actor = session.active_human
            if actor is not None and self._my_seats is not None:
                return f"Waiting for {actor.name}..."
        return "Waiting for the other players..."

    def _default_amount(self, legal: LegalActions) -> int:
        if legal.can_raise:
            return legal.min_raise_to
        if legal.can_bet:
            return legal.min_bet
        return 0

    # ------------------------------------------------------------------
    # coach
    # ------------------------------------------------------------------
    def _refresh_coach(self, session, style: str) -> None:
        panel = self.query_one(CoachPanel)
        dial = self.app.dial

        if self._hand_over and self._outcome is not None:
            panel.show(None, dial, hidden_reason=self._result_markup(session))
            return

        if self._may_act():
            actor = session.active_human
            key = (
                session.hand.hand_id,
                session.hand.street,
                tuple(session.hand.board),
                actor.index if actor else -1,
                len(session.hand.events),
            )
            if key != self._advice_key:
                self._advice_key = key
                self._advice = build_advice(
                    session.hand,
                    actor.index,
                    dial,
                    rng=self.app.rng,
                    open_ranges=self.app.content.open_ranges,
                    three_bet_ranges=self.app.content.three_bet_ranges,
                )
            panel.show(self._advice, dial)
            return

        if dial.enabled and self.app.dial.level > 0:
            panel.show(None, dial, hidden_reason=self._waiting_markup(session))
        else:
            panel.show(None, dial)

    def _waiting_markup(self, session) -> str:
        hero = next(
            (s for s in session.seats if s.index in session.human_seats), None
        )
        if hero is not None and hero.folded:
            return "You folded this hand. The coach has nothing to add."
        return "Waiting for the other players to act."

    def _result_markup(self, session) -> str:
        outcome = self._outcome
        if outcome is None:
            return "Hand complete. Press N for the next hand."
        lines: list[str] = [f"[{theme.ACCENT}][b]Hand complete[/b][/]"]
        board = " ".join(theme.card_markup(c) for c in outcome.board)
        if board:
            lines.append(f"Board: {board}")
        for index in session.human_seats:
            seat = session.seats[index]
            net = outcome.net.get(index, 0)
            lines.append(f"{seat.name}: {theme.result_markup(net)}  (stack {seat.stack:,})")
        if session.hands_played > 0:
            for index, hole in sorted(outcome.reveal.items()):
                if index in session.human_seats:
                    continue
                from ...engine import evaluator as E

                score = E.evaluate(list(hole) + list(outcome.board)) if len(outcome.board) == 5 else None
                shown = f" ({E.hand_name(score)})" if score else ""
                lines.append(
                    f"[{theme.TEXT_DIM}]{session.seats[index].name} showed[/] "
                    + " ".join(theme.card_markup(c) for c in hole)
                    + f"[{theme.TEXT_DIM}]{shown}[/]"
                )
        if self.app.dial.shows_review:
            for line in review_hand(
                outcome, session.seats, self._hero_seat(session), self.app.dial
            ):
                lines.append(f"[{theme.BLUE}]•[/] {line}")
        lines.append(f"[{theme.TEXT_DIM}]Press [b]N[/] for the next hand, "
                     f"[b]V[/] for the full review.[/]")
        return "\n".join(lines)

    def _hero_seat(self, session) -> int:
        return session.human_seats[0] if session.human_seats else 0

    # ------------------------------------------------------------------
    # music chip
    # ------------------------------------------------------------------
    def _tick_music(self) -> None:
        try:
            state = self.app.music.refresh()
        except Exception:  # pragma: no cover - music must never break poker
            return
        self._chip_text = state.short(26) if state.title else state.source_label
        self.query_one(MusicChip).show(self._chip_text, self._levels, self._live)

    def _tick_bars(self) -> None:
        try:
            levels, live = self.app.music.levels(12)
        except Exception:  # pragma: no cover
            levels, live = [0.0] * 12, False
        if not live:
            self._phase += 0.25
            levels = idle_wave(12, self._phase)
        self._levels, self._live = levels, live
        self.query_one(MusicChip).show(self._chip_text, levels, live)

    def on_music_chip_open_panel(self, event: MusicChip.OpenPanel) -> None:
        self.app.open_music()

    def on_music_chip_cycle_source(self, event: MusicChip.CycleSource) -> None:
        self.app.music.cycle_source()
        self._tick_music()

    # ------------------------------------------------------------------
    # acting
    # ------------------------------------------------------------------
    def _act(self, action: str, amount: int = 0) -> None:
        session = self.app.session
        if not self._may_act():
            self.app.notify("It is not your turn.", severity="warning")
            return
        actor = session.active_human
        try:
            session.human_action(action, amount)
        except IllegalAction as exc:
            self.app.notify(str(exc), severity="error")
            self.refresh_table()
            return
        if actor is not None:
            self._log(f"→ {actor.name} {action}"
                      + (f" {amount:,}" if amount else ""), "action")
        if self.app.player.profile.sound:
            self.app.bell()
        self._pending = ""
        self._advice_key = None
        self._log_new_events()
        self._progress()

    def action_fold(self) -> None:
        self._act("fold")

    def action_check_call(self) -> None:
        session = self.app.session
        if not self._may_act():
            self.app.notify("It is not your turn.", severity="warning")
            return
        legal = session.legal_actions()
        if legal.can_check:
            self._act("check")
        elif legal.can_call:
            self._act("call", legal.call_amount)

    def action_raise_mode(self) -> None:
        self._begin_amount("raise")

    def action_bet_mode(self) -> None:
        self._begin_amount("bet")

    def action_all_in(self) -> None:
        session = self.app.session
        if not self._may_act():
            return
        legal = session.legal_actions()
        if not legal.can_all_in:
            self.app.notify("You cannot raise here.", severity="warning")
            return
        action = "raise" if legal.can_raise else "bet"
        self._act(action, legal.max_raise_to)

    def _begin_amount(self, kind: str) -> None:
        session = self.app.session
        bar = self.query_one(ActionBar)
        if not self._may_act():
            self.app.notify("It is not your turn.", severity="warning")
            return
        legal = session.legal_actions()
        if kind == "bet" and not legal.can_bet and legal.can_raise:
            kind = "raise"
        if kind == "raise" and not legal.can_raise and legal.can_bet:
            kind = "bet"
        if self._pending == kind:
            self._submit_amount()
            return
        if kind == "bet" and not legal.can_bet:
            self.app.notify(
                "You cannot bet here - there is already a bet in front of you. "
                "Press R to raise, or C to call.",
                severity="warning",
            )
            return
        if kind == "raise" and not legal.can_raise:
            reason = (
                "There is nothing to raise yet - press B to bet."
                if legal.can_bet
                else "Raising is not available here (an all-in for less does not "
                "reopen the betting)."
            )
            self.app.notify(reason, severity="warning")
            return
        self._pending = kind
        legal_now = legal
        suggestion = self._pot_suggestion(legal_now) if kind in ("bet", "raise") else 0
        bar.suggest(suggestion or self._default_amount(legal_now))
        bar.focus_amount()
        self._refresh_actions()

    def _pot_suggestion(self, legal: LegalActions) -> int:
        session = self.app.session
        if session is None or session.hand is None:
            return 0
        pot = session.hand.total_pot
        target = pot + legal.call_amount
        low = legal.min_bet if legal.can_bet else legal.min_raise_to
        high = legal.max_raise_to
        return max(low, min(high, target))

    def action_confirm_amount(self) -> None:
        if self._pending:
            self._submit_amount()

    def action_cancel_amount(self) -> None:
        if self._pending:
            self._pending = ""
            bar = self.query_one(ActionBar)
            bar.clear_amount()
            bar.blur_amount()
            self._refresh_actions()
            return
        self.action_leave_table()

    def _submit_amount(self) -> None:
        session = self.app.session
        kind = self._pending
        if session is None or not kind or not self._may_act():
            self._pending = ""
            return
        legal = session.legal_actions()
        bar = self.query_one(ActionBar)
        amount = bar.amounts()
        minimum = legal.min_bet if kind == "bet" else legal.min_raise_to
        if amount <= 0:
            amount = minimum
        amount = max(minimum, min(amount, legal.max_raise_to))
        self._pending = ""
        bar.clear_amount()
        bar.blur_amount()
        self._act(kind, amount)

    # -- quick amounts -------------------------------------------------
    def _set_amount(self, amount: int) -> None:
        legal = self._legal_or_none()
        if legal is None:
            return
        low = legal.min_bet if legal.can_bet and not legal.can_raise else legal.min_raise_to
        high = legal.max_raise_to or legal.min_bet
        self.query_one(ActionBar).suggest(max(low, min(high, amount)))

    def _legal_or_none(self) -> LegalActions | None:
        session = self.app.session
        if not self._may_act():
            return None
        return session.legal_actions()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        identifier = event.button.id or ""
        if identifier == "btn-fold":
            self.action_fold()
        elif identifier == "btn-call":
            self.action_check_call()
        elif identifier == "btn-raise":
            self._begin_amount("raise" if (self._legal_or_none() or LegalActions()).can_raise else "bet")
        elif identifier == "btn-min":
            self._set_amount(0)
            legal = self._legal_or_none()
            if legal:
                self._set_amount(self._default_amount(legal))
        elif identifier in ("btn-half", "btn-pot", "btn-max"):
            legal = self._legal_or_none()
            if legal is None:
                return
            pot = self.app.session.hand.total_pot if self.app.session.hand else 0
            if identifier == "btn-half":
                self._set_amount((pot + legal.call_amount) // 2 or legal.min_raise_to)
            elif identifier == "btn-pot":
                self._set_amount(pot + legal.call_amount)
            else:
                self._set_amount(legal.max_raise_to)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "bet-input":
            self._submit_amount()

    # ------------------------------------------------------------------
    # hand management
    # ------------------------------------------------------------------
    def action_next_hand(self) -> None:
        session = self.app.session
        if session is None:
            return
        if not self._hand_over:
            self.app.notify("The hand is still running.", severity="warning")
            return
        if self._busted_humans(session):
            # An unresolved bust is still on screen; settle it first.
            self._handle_busts()
            return
        if not self._humans_in_play(session):
            self.app.notify("Nobody is left at this table. Leaving.", severity="warning")
            self.app.leave_table()
            return
        if not session.live:
            self.app.notify(
                "Not enough players with chips left. Leaving.", severity="warning"
            )
            self.app.leave_table()
            return
        session.start_hand()
        self._hand_over = False
        self._outcome = None
        self._logged_hand = None
        self._advice_key = None
        self._pending = ""
        self.query_one(ActionBar).clear_amount()
        self._progress()

    def _busted_humans(self, session) -> list[int]:
        return [
            index
            for index in session.human_seats
            if (self._my_seats is None or index in self._my_seats)
            and session.seats[index].stack <= 0
            and not session.seats[index].sitting_out
        ]

    def _humans_in_play(self, session) -> bool:
        return any(not session.seats[i].sitting_out for i in session.human_seats)

    def _handle_busts(self) -> None:
        session = self.app.session
        if session is None:
            return
        for index in self._busted_humans(session):
            seat = session.seats[index]
            holder = session.holder_for(index)
            can_rebuy = holder.bankroll.chips >= session.level.buy_in
            options: list[tuple[str, str]] = []
            if can_rebuy:
                options.append(("rebuy", f"Buy in again ({session.level.buy_in:,})"))
            else:
                options.append(
                    ("reset", f"Reset bankroll to 100,000 chips")
                )
            options.append(("sitout", "Sit out for the rest of the night"))
            options.append(("leave", "Leave the table"))
            self.app.push_screen(
                ChoiceScreen(
                    f"{seat.name} is out of chips",
                    options,
                    detail=f"{holder.bankroll.chips:,} chips in the bankroll.",
                ),
                lambda choice, seat_index=index: self._bust_choice(seat_index, choice),
            )
            return

    def _bust_choice(self, seat_index: int, choice: str | None) -> None:
        session = self.app.session
        if session is None:
            return
        seat = session.seats[seat_index]
        if choice == "rebuy":
            taken = session.rebuy(seat_index)
            self._log(f"{seat.name} buys in again for {taken:,}", "info")
        elif choice == "reset":
            self.app.reset_bankroll()
            taken = session.rebuy(seat_index)
            self._log(f"{seat.name} resets and buys in for {taken:,}", "info")
        elif choice == "leave":
            self.app.leave_table()
            return
        else:
            seat.sitting_out = True
            self._log(f"{seat.name} sits out", "info")
        self.refresh_table()
        self._handle_busts()

    def action_assist_menu(self) -> None:
        from .panels import AssistScreen

        self.app.push_screen(
            AssistScreen(self.app.dial.level),
            self._assist_chosen,
        )

    def _assist_chosen(self, level: int | None) -> None:
        if level is None:
            return
        self.app.set_assist(level)
        self._advice_key = None
        self.refresh_table()

    def action_review(self) -> None:
        if self._outcome is None:
            self.app.notify("No finished hand to review yet.", severity="warning")
            return
        from .panels import ReviewScreen

        self.app.push_screen(
            ReviewScreen(self._outcome, self.app.session, self._hero_seat(self.app.session))
        )

    def action_toggle_log(self) -> None:
        panel = self.query_one(LogPanel)
        panel.display = not panel.display

    def action_leave_table(self) -> None:
        session = self.app.session
        if session is None:
            self.app.pop_screen()
            return
        live = session.hand is not None and session.hand.street is not Street.COMPLETE
        hero_in_hand = any(
            not session.seats[i].folded for i in session.human_seats
        )
        if live and hero_in_hand:
            self.app.push_screen(
                ConfirmScreen(
                    "Leave with a hand in progress?",
                    detail="Your cards are folded and your chips come back to your "
                    "bankroll.",
                ),
                lambda ok: self.app.leave_table() if ok else None,
            )
        else:
            self.app.leave_table()

    # -- music shortcuts ----------------------------------------------
    def action_music_toggle(self) -> None:
        self.app.music.toggle_play()
        self.app.notify_playing()

    def action_volume_down(self) -> None:
        self.app.music.adjust_volume(-5)
        self.app.notify_playing()

    def action_volume_up(self) -> None:
        self.app.music.adjust_volume(5)
        self.app.notify_playing()


__all__ = ["BOARD", "CLOCK", "TableScreen", "ring_rows"]
