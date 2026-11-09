"""A playable session: engine, bots and bankroll wired together.

The UI only ever talks to a :class:`GameSession`. It handles

* seating humans and bots at a table,
* moving the buy-in out of the bankroll and the final stack back in,
* running bot turns automatically once a human acts,
* recording each finished hand into stats,
* and exposing a snapshot the UI can render.

Hot-seat and solo games use this directly. Networked games use it on the host
side and mirror the public state out to clients.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .data import DataStore, PlayerData, analyse_hand
from .data.ladder import TableLevel
from .data.tables import TableSeat, new_sit_id
from .engine import cards as C
from .engine.bots import (
    BotProfile,
    act_for,
    profile_for,
    suggest_bot_names,
)
from .engine.game import (
    Action,
    Hand,
    HandEvent,
    IllegalAction,
    LegalActions,
    Seat,
    Street,
    Table,
)


class Mode(str, Enum):
    SOLO = "solo"
    HOTSEAT = "hotseat"
    HOST = "host"
    CLIENT = "client"


@dataclass
class HandOutcome:
    """What happened in the hand just finished, for the review screen."""

    hand_id: int
    board: list[int]
    reveal: dict[int, list[int]]
    totals: dict[int, int]
    net: dict[int, int]
    showdown: bool
    winning_score: tuple[int, ...] | None = None
    went_to_showdown: list[int] = field(default_factory=list)


class GameSession:
    """One table, played until the player leaves."""

    def __init__(
        self,
        player: PlayerData,
        level: TableLevel,
        *,
        store: DataStore | None = None,
        bot_level: str = "medium",
        bot_count: int = 4,
        human_names: list[str] | None = None,
        human_players: list[PlayerData] | None = None,
        seed: int | None = None,
        auto_rebuy_bots: bool = True,
        mode: Mode = Mode.SOLO,
    ) -> None:
        self.store = store
        self.player = player
        self.level: TableLevel = level
        self.mode = mode
        self.bot_level = bot_level
        self.bot_count = bot_count
        self.bot_profile: BotProfile = profile_for(bot_level)
        self.auto_rebuy_bots = auto_rebuy_bots
        self.rng = random.Random(seed)

        self.buy_in = min(level.buy_in, player.bankroll.chips)
        self.buy_in_taken = player.bankroll.buy_in(
            self.buy_in, f"buy-in {level.name}"
        )
        if store is not None:
            store.bankroll_store.save_bankroll(player.bankroll)

        self.seats: list[Seat] = []
        self.human_seats: list[int] = []
        self._seat_players: dict[int, PlayerData] = {}
        names = human_names or [player.profile.name]
        buyers = human_players or [player]
        self._build_seats(names, buyers)

        self.table = Table(
            self.seats, small_blind=level.small_blind, big_blind=level.big_blind
        )
        self.table.button = self._random_button()
        self.hand: Hand | None = None
        self.last_outcome: HandOutcome | None = None
        self.hands_played = 0
        self._context_seat: int | None = None
        #: One id per sitting, so a recovery after a crash can never pay twice.
        self._sit_id = new_sit_id()
        # Write the stack down before a card is dealt: if this process dies, the
        # next start puts the chips back instead of losing them with the table.
        self._mark_table()

    # ------------------------------------------------------------------
    # seating
    # ------------------------------------------------------------------

    def _build_seats(
        self, human_names: list[str], human_players: list[PlayerData]
    ) -> None:
        """Seat every human first, then fill the remaining chairs with bots.

        Note the human always occupies seat 0 and therefore sits first in the
        rotation; the bots fill the remaining seats. Each human pays their own
        buy-in out of their own bankroll, so a hot-seat friend does not spend
        somebody else's chips.
        """
        humans: list[tuple[str, PlayerData]] = []
        for position, name in enumerate(human_names):
            holder = (
                human_players[position]
                if position < len(human_players)
                else self.player
            )
            humans.append((name, holder))
        if not humans:
            humans = [(self.player.profile.name, self.player)]

        bot_names = suggest_bot_names(
            self._bot_count_for(len(humans)), taken=[name for name, _ in humans]
        )

        ordered: list[tuple[str, bool, PlayerData | None]] = [
            (name, True, holder) for name, holder in humans
        ]
        ordered.extend((name, False, None) for name in bot_names)

        for index, (name, is_human, data) in enumerate(ordered[:9]):
            seat = Seat(
                index=index,
                name=name,
                stack=self.buy_in,
                is_human=is_human,
                is_bot=not is_human,
                level="human" if is_human else self.bot_profile.level,
                bot_name="" if is_human else name,
                profile_id=(
                    data.profile.id if data else (self.player.profile.id if is_human else "")
                ),
            )
            self.seats.append(seat)
            if not is_human:
                continue
            holder = data or self.player
            self.human_seats.append(index)
            self._seat_players[index] = holder
            if holder is not self.player:
                taken = holder.bankroll.buy_in(
                    min(self.buy_in, holder.bankroll.chips), f"buy-in {self.level.name}"
                )
                seat.stack = taken

    def _bot_count_for(self, humans: int) -> int:
        total = min(9, max(2, humans + self.bot_count))
        return max(0, total - humans)

    def _random_button(self) -> int:
        return self.rng.randrange(len(self.seats))

    # ------------------------------------------------------------------
    # hand flow
    # ------------------------------------------------------------------

    @property
    def live(self) -> bool:
        """True while there are enough players with chips to deal a hand."""
        return len(self.seats_in_play) >= 2

    @property
    def seats_in_play(self) -> list[Seat]:
        return [s for s in self.seats if s.stack > 0 and not s.sitting_out]

    def start_hand(self) -> Hand:
        if not self.live:
            raise RuntimeError("not enough players with chips to deal")
        self.hand = self.table.start_hand(seed=self.rng.randbytes(32))
        self.hands_played += 1
        return self.hand

    def actor(self) -> Seat | None:
        return self.hand.actor() if self.hand else None

    def legal_actions(self, seat_index: int | None = None) -> LegalActions:
        if self.hand is None:
            return LegalActions(can_fold=False)
        return self.hand.legal_actions(seat_index)

    @property
    def awaiting_human(self) -> bool:
        """True when it is a human's turn and the hand is live."""
        if self.hand is None or self.hand.street is Street.COMPLETE:
            return False
        actor = self.actor()
        return bool(actor and actor.is_human)

    @property
    def active_human(self) -> Seat | None:
        actor = self.actor()
        return actor if actor and actor.is_human else None

    def human_action(self, action: str | Action, amount: int = 0) -> None:
        """Apply a human's action, then let the bots respond."""
        if self.hand is None:
            raise IllegalAction("no hand in progress")
        actor = self.actor()
        if actor is None or not actor.is_human:
            raise IllegalAction("it is not a human player's turn")
        self.hand.act(action, amount)
        self.run_bots()

    def run_bots(self, max_steps: int = 500) -> None:
        """Play out every bot turn until a human must act or the hand ends."""
        if self.hand is None:
            return
        steps = 0
        while self.hand.street is not Street.COMPLETE:
            actor = self.actor()
            if actor is None or actor.is_human:
                return
            profile = profile_for(actor.level if actor.level != "human" else self.bot_level)
            act_for(self.hand, actor.index, profile, self.rng)
            steps += 1
            if steps > max_steps:
                raise RuntimeError("bot loop failed to settle")

    def finish_hand(self) -> HandOutcome | None:
        """Close out the hand: stats, bankroll and the review snapshot."""
        if self.hand is None or self.hand.street is not Street.COMPLETE:
            return None
        hand = self.hand
        if self.last_outcome is not None and self.last_outcome.hand_id == hand.hand_id:
            return self.last_outcome
        self.table.finish_hand()

        outcome = HandOutcome(
            hand_id=hand.hand_id,
            board=list(hand.board),
            reveal=dict(hand.result.revealed) if hand.result else {},
            totals=dict(hand.result.totals) if hand.result else {},
            net={},
            showdown=bool(hand.result and hand.result.showdown),
            winning_score=hand.result.winning_score if hand.result else None,
            went_to_showdown=list(hand.result.went_to_showdown_hands) if hand.result else [],
        )

        for seat in self.seats:
            committed = seat.committed_total
            gross = outcome.totals.get(seat.index, 0)
            outcome.net[seat.index] = gross - committed

        # Fold the hand into the human player's stats.
        for index in self.human_seats:
            holder = self._seat_players.get(index, self.player)
            record, actions = analyse_hand(hand, index, table=self.level.name)
            record.bot_level = self.bot_level
            for bot in self.seats:
                if bot.is_bot:
                    record.bot_level = bot.level
                    break
            holder.stats.record(record, actions)

        if self.store is not None:
            self._persist()

        self.last_outcome = outcome
        self._housekeeping()
        return outcome

    def _housekeeping(self) -> None:
        """Re-buy bots so the table stays full, and rotate the button."""
        if self.auto_rebuy_bots:
            for seat in self.seats:
                if seat.is_bot and seat.stack <= 0:
                    seat.stack = self.buy_in or self.level.buy_in
        self.table.advance_button()

    def next_hand(self) -> Hand:
        if not self.live:
            raise RuntimeError("no players left with chips")
        return self.start_hand()

    # ------------------------------------------------------------------
    # bankroll
    # ------------------------------------------------------------------

    @property
    def table_chips(self) -> int:
        return sum(s.stack for s in self.seats if s.is_human)

    def holder_for(self, seat_index: int) -> PlayerData:
        """The bankroll/stats holder behind a human seat."""
        return self._seat_players.get(seat_index, self.player)

    def rebuy(self, seat_index: int) -> int:
        """Buy the holder of ``seat_index`` back in. Returns the chips added."""
        seat = self.seats[seat_index]
        if seat_index not in self.human_seats:
            return 0
        holder = self.holder_for(seat_index)
        amount = min(self.level.buy_in, holder.bankroll.chips)
        if amount <= 0:
            return 0
        taken = holder.bankroll.buy_in(amount, f"rebuy {self.level.name}")
        seat.stack += taken
        seat.sitting_out = False
        if self.store is not None:
            self._persist()
        return taken

    def leave(self) -> int:
        """Cash out every human stack back into their bankrolls."""
        returned = 0
        for index in self.human_seats:
            holder = self._seat_players.get(index, self.player)
            stack = self.seats[index].stack
            holder.bankroll.cash_out(stack, f"left {self.level.name}")
            returned += stack
            self.seats[index].stack = 0
        if self.store is not None:
            self._persist()
            if self.mode is not Mode.CLIENT:
                # The chips are back: there is no sitting left to recover.
                self.store.clear_table_seat(self.player.profile.id)
        return returned

    def _local_seat_index(self) -> int | None:
        """The seat the profile on this machine is sitting in."""
        for index in self.human_seats:
            if self._seat_players.get(index, self.player) is self.player:
                return index
        return None

    def _mark_table(self) -> None:
        """Record this profile's stack on disk, so a crash cannot lose it."""
        if self.store is None or self.mode is Mode.CLIENT or self.player.guest:
            return
        index = self._local_seat_index()
        if index is None:
            return
        self.store.save_table_seat(
            TableSeat(
                profile_id=self.player.profile.id,
                level=self.level.name,
                buy_in=self.buy_in,
                stack=self.seats[index].stack,
                sit_id=self._sit_id,
            )
        )

    def _persist(self) -> None:
        if self.store is None:
            return
        self.store.save_profile(self.player.profile)
        self.store.bankroll_store.save_bankroll(self.player.bankroll)
        self.store.stats_store.save_stats(self.player.stats)
        for holder in self._seat_players.values():
            # Guests are hot-seat friends with throwaway chips: never on disk.
            if holder is not self.player and not holder.guest:
                self.store.bankroll_store.save_bankroll(holder.bankroll)
                self.store.stats_store.save_stats(holder.stats)
        # Refresh the sitting note with the stack as it stands now.
        self._mark_table()

    # ------------------------------------------------------------------
    # rendering helpers
    # ------------------------------------------------------------------

    def state(self, viewer: int | None = None) -> dict[str, Any]:
        """A snapshot for the UI or for the wire."""
        if self.hand is None:
            return {
                "in_hand": False,
                "seats": [
                    {
                        "index": s.index,
                        "name": s.name,
                        "stack": s.stack,
                        "is_human": s.is_human,
                        "is_bot": s.is_bot,
                        "hole": [],
                        "folded": False,
                        "all_in": False,
                        "sitting_out": s.stack <= 0,
                        "last_action": "",
                    }
                    for s in self.seats
                ],
                "board": [],
                "pot": 0,
                "level": self.level.name,
                "bankroll": self.player.bankroll.chips,
                "hand_id": self.hands_played,
            }
        state = self.hand.public_state(viewer=viewer)
        state["in_hand"] = True
        state["level"] = self.level.name
        state["bankroll"] = self.player.bankroll.chips
        state["total_table_chips"] = self.table.chips_in_play()
        for entry in state["seats"]:
            entry["is_human"] = self.seats[entry["index"]].is_human
        return state

    def visible_hole(self, seat_index: int, viewer: int | None) -> list[int]:
        """Cards a viewer may see, honouring hot-seat hand hiding."""
        seat = self.seats[seat_index]
        if viewer is not None and seat_index == viewer:
            return list(seat.hole)
        if self.hand and self.hand.result and seat_index in self.hand.result.revealed:
            return list(self.hand.result.revealed[seat_index])
        return []

    def recent_events(self, count: int = 12) -> list[HandEvent]:
        if self.hand is None:
            return []
        return self.hand.events[-count:]

    # ------------------------------------------------------------------
    # networking support
    # ------------------------------------------------------------------

    def apply_remote(self, seat_index: int, action: str, amount: int = 0) -> None:
        """Apply an action that arrived from a remote human player."""
        if self.hand is None:
            raise IllegalAction("no hand in progress")
        actor = self.actor()
        if actor is None or actor.index != seat_index:
            raise IllegalAction("not that player's turn")
        self.hand.act(action, amount)

    def seat_for_profile(self, profile_id: str) -> Seat | None:
        for seat in self.seats:
            if seat.profile_id == profile_id and seat.is_human:
                return seat
        return None

    def replace_seat_with_bot(self, seat_index: int) -> Seat:
        """Take over a disconnected player's seat."""
        seat = self.seats[seat_index]
        seat.is_human = False
        seat.is_bot = True
        seat.level = self.bot_level
        seat.profile_id = ""
        return seat
