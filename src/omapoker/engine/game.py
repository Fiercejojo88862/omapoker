"""No-Limit Texas Hold'em table state machine.

The rules implemented here are the standard casino ones:

* 2–9 handed, button moves clockwise.
* Blinds scale with the table buy-in (see :mod:`omapoker.data.ladder`), and are
  posted as all-in if the stack cannot cover them.
* A raise must increase the bet by at least the size of the last full raise.
* An all-in for **less** than a full raise is allowed but does **not** reopen
  the betting for players who have already acted; they may only call or fold.
* Uncalled bets are returned. This falls out of side-pot construction for free,
  because the top level ends up with a single eligible player.
* Multiple all-ins produce proper side pots; split pots give the odd chip to the
  first eligible seat left of the button.

Chip conservation is an invariant: every seat's stack plus every pot always sums
to the chips the table started with. The test suite hammers this with thousands
of random hands.
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterator, Sequence

from . import cards as C
from . import evaluator as E


class Street(str, Enum):
    PREFLOP = "preflop"
    FLOP = "flop"
    TURN = "turn"
    RIVER = "river"
    SHOWDOWN = "showdown"
    COMPLETE = "complete"


class Action(str, Enum):
    FOLD = "fold"
    CHECK = "check"
    CALL = "call"
    BET = "bet"
    RAISE = "raise"
    POST_SMALL_BLIND = "post_sb"
    POST_BIG_BLIND = "post_bb"


BOARD_COUNTS = {Street.PREFLOP: 0, Street.FLOP: 3, Street.TURN: 4, Street.RIVER: 5}


@dataclass
class Seat:
    """A player's seat at one table. Stacks persist across hands."""

    index: int
    name: str
    stack: int
    is_human: bool = True
    is_bot: bool = False
    level: str = "casual"
    profile_id: str = ""
    bot_name: str = ""
    #: Per-hand state, reset by :meth:`Hand.reset_seats`.
    hole: list[int] = field(default_factory=list)
    folded: bool = False
    all_in: bool = False
    committed_street: int = 0
    committed_total: int = 0
    has_acted: bool = False
    can_raise: bool = True
    last_action: str = ""
    sitting_out: bool = False

    @property
    def in_hand(self) -> bool:
        return not self.folded and not self.sitting_out

    def reset(self) -> None:
        self.hole = []
        self.folded = False
        self.all_in = False
        self.committed_street = 0
        self.committed_total = 0
        self.has_acted = False
        self.can_raise = True
        self.last_action = ""


@dataclass
class HandEvent:
    """One thing that happened, for the log, the UI and the coach."""

    kind: str
    seat: int | None = None
    text: str = ""
    amount: int = 0
    street: Street | None = None
    meta: dict = field(default_factory=dict)


@dataclass
class LegalActions:
    can_fold: bool = True
    can_check: bool = False
    can_call: bool = False
    call_amount: int = 0
    can_bet: bool = False
    min_bet: int = 0
    can_raise: bool = False
    min_raise_to: int = 0
    max_raise_to: int = 0

    @property
    def can_all_in(self) -> bool:
        return self.max_raise_to > 0

    def describe(self) -> str:
        parts = []
        if self.can_fold:
            parts.append("fold")
        if self.can_check:
            parts.append("check")
        if self.can_call:
            parts.append(f"call {self.call_amount}")
        if self.can_bet:
            parts.append(f"bet {self.min_bet}-{self.max_raise_to}")
        if self.can_raise:
            parts.append(f"raise to {self.min_raise_to}-{self.max_raise_to}")
        return ", ".join(parts)


@dataclass
class Pot:
    amount: int
    eligible: list[int]
    label: str = ""

    @property
    def is_side_pot(self) -> bool:
        return bool(self.label)


@dataclass
class Payout:
    seat: int
    amount: int
    pot_label: str
    hand_score: tuple[int, ...] | None = None


@dataclass
class HandResult:
    payouts: list[Payout] = field(default_factory=list)
    showdown: bool = False
    winning_score: tuple[int, ...] | None = None
    revealed: dict[int, list[int]] = field(default_factory=dict)
    went_to_showdown_hands: list[int] = field(default_factory=list)

    def net_for(self, seat: int) -> int:
        return sum(p.amount for p in self.payouts if p.seat == seat)

    @property
    def totals(self) -> dict[int, int]:
        """Gross chips paid to each seat across every pot."""
        result: dict[int, int] = {}
        for payout in self.payouts:
            result[payout.seat] = result.get(payout.seat, 0) + payout.amount
        return result


@dataclass
class ActionRecord:
    seat: int
    action: str
    amount: int
    street: Street
    pot_before: int


class IllegalAction(Exception):
    pass


class Hand:
    """A single hand of No-Limit Hold'em."""

    def __init__(
        self,
        seats: Sequence[Seat],
        button: int,
        small_blind: int,
        big_blind: int,
        deck: Sequence[int] | None = None,
        seed: bytes | None = None,
        hand_id: int = 0,
        ante: int = 0,
    ) -> None:
        if len(seats) < 2:
            raise ValueError("need at least two seats")
        if big_blind <= 0 or small_blind <= 0:
            raise ValueError("blinds must be positive")
        self.seats: list[Seat] = list(seats)
        self.n = len(self.seats)
        self.button = button % self.n
        self.small_blind = small_blind
        self.big_blind = big_blind
        self.ante = ante
        self.hand_id = hand_id

        self.players = [s for s in self.seats if not s.sitting_out]

        # --- provable-shuffle support ------------------------------------
        if seed is None:
            seed = os.urandom(32)
        self.seed = seed
        self.rng = random.Random(seed)
        if deck is None:
            self.deck = list(C.FULL_DECK)
            C.shuffle(self.deck, self.rng)
        else:
            self.deck = list(deck)
            if len(self.deck) != 52 or len(set(self.deck)) != 52:
                raise ValueError("a deck must be a permutation of 52 cards")
        #: The full deck as shuffled, kept for commit-reveal verification even
        #: after cards have been dealt off the working deck.
        self.original_deck = list(self.deck)
        self.deck_commit = C.commit_for_deck(self.original_deck)

        # --- table state --------------------------------------------------
        self.board: list[int] = []
        self.pot = 0
        self.street = Street.PREFLOP
        self.current_bet = 0
        self.last_full_raise = big_blind
        self.min_raise_to = big_blind
        self.to_act: int | None = None
        self.events: list[HandEvent] = []
        self.actions: list[ActionRecord] = []
        self.result: HandResult | None = None
        self.started = False

        self._sb_seat, self._bb_seat = self._blind_seats()

    # ------------------------------------------------------------------
    # setup
    # ------------------------------------------------------------------

    def _blind_seats(self) -> tuple[int, int]:
        if self.n == 2:
            return self.button, (self.button + 1) % self.n
        return (self.button + 1) % self.n, (self.button + 2) % self.n

    def start(self) -> None:
        """Post blinds, deal hole cards and hand the action to the first player."""
        if self.started:
            raise RuntimeError("hand already started")
        self.started = True
        for seat in self.seats:
            seat.reset()

        if self.ante:
            for seat in self.players:
                paid = min(seat.stack, self.ante)
                if paid:
                    seat.stack -= paid
                    seat.committed_total += paid
                    if seat.stack == 0:
                        seat.all_in = True
                    self.pot += paid
                    self.actions.append(
                        ActionRecord(seat.index, "ante", paid, Street.PREFLOP, self.pot)
                    )
            if self.ante:
                self._log("ante", None, f"Antes: {self.ante:,} each", amount=self.ante)
        self._post_blind(self._sb_seat, self.small_blind, Action.POST_SMALL_BLIND)
        self._post_blind(self._bb_seat, self.big_blind, Action.POST_BIG_BLIND)

        # The big blind sets the preflop bet level even when a blind is short
        # (players must still call the full blind, or go all-in for less).
        self.current_bet = max(
            self.big_blind,
            max((s.committed_street for s in self.seats), default=0),
        )
        self.last_full_raise = self.big_blind
        self.min_raise_to = self.current_bet + self.last_full_raise

        for _ in range(2):
            for seat in self._deal_order():
                seat.hole.append(C.deal(self.deck, 1)[0])

        for seat in self.seats:
            seat.has_acted = False
            seat.can_raise = True

        self._log("hand_start", None, f"Hand #{self.hand_id} begins", street=Street.PREFLOP)
        self.to_act = self._next_to_act(self._bb_seat)
        if self.to_act is None:
            self._advance_from(None)

    def _deal_order(self) -> Iterator[Seat]:
        """Deal starting left of the button, clockwise."""
        for offset in range(1, self.n + 1):
            seat = self.seats[(self.button + offset) % self.n]
            if not seat.sitting_out:
                yield seat

    def _post_blind(self, seat_index: int, amount: int, action: Action) -> None:
        seat = self.seats[seat_index]
        if seat.sitting_out:
            # A sitting-out player's blind is skipped entirely; the hand simply
            # plays short-handed from that seat.
            return
        paid = self._take(seat, min(seat.stack, amount), action, f"{action.value}")
        self._log(
            "blind",
            seat.index,
            f"{seat.name} posts {paid:,} ({'small' if 'small' in action.value else 'big'} blind)",
            amount=paid,
            street=Street.PREFLOP,
        )

    def _take(self, seat: Seat, amount: int, action: Action, note: str) -> int:
        """Move ``amount`` from a stack into the seat's commitment."""
        amount = max(0, min(amount, seat.stack))
        seat.stack -= amount
        seat.committed_street += amount
        seat.committed_total += amount
        if seat.stack == 0:
            seat.all_in = True
        if amount:
            self.actions.append(
                ActionRecord(seat.index, action.value, amount, self.street, self.total_pot)
            )
        return amount

    # ------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------

    @property
    def contenders(self) -> list[Seat]:
        """Seats that can still win the pot."""
        return [s for s in self.seats if not s.folded and not s.sitting_out]

    @property
    def total_pot(self) -> int:
        return self.pot + sum(s.committed_street for s in self.seats)

    @property
    def street_pot(self) -> int:
        return sum(s.committed_street for s in self.seats)

    def actor(self) -> Seat | None:
        return self.seats[self.to_act] if self.to_act is not None else None

    def legal_actions(self, seat_index: int | None = None) -> LegalActions:
        idx = self.to_act if seat_index is None else seat_index
        if idx is None or self.street in (Street.COMPLETE, Street.SHOWDOWN):
            return LegalActions(can_fold=False)
        seat = self.seats[idx]
        if seat.folded or seat.sitting_out or seat.stack == 0:
            return LegalActions(can_fold=False)

        owed = self.current_bet - seat.committed_street
        legal = LegalActions(can_fold=True)

        if owed <= 0:
            legal.can_check = True
        else:
            legal.can_call = True
            legal.call_amount = min(owed, seat.stack)

        max_to = seat.committed_street + seat.stack  # all-in total

        if self.current_bet == 0:
            # Opening bet. Minimum is a big blind unless the stack is shorter.
            if seat.stack > 0:
                legal.can_bet = True
                legal.min_bet = min(self.big_blind, seat.stack)
                legal.max_raise_to = max_to
        else:
            if seat.can_raise and max_to > self.current_bet:
                legal.can_raise = True
                legal.min_raise_to = min(self.current_bet + self.last_full_raise, max_to)
                legal.max_raise_to = max_to
        return legal

    # ------------------------------------------------------------------
    # taking action
    # ------------------------------------------------------------------

    def act(self, action: str | Action, amount: int = 0) -> None:
        if self.to_act is None:
            raise IllegalAction("no player is to act")
        seat = self.seats[self.to_act]
        act = Action(action)
        legal = self.legal_actions(self.to_act)

        if act is Action.FOLD:
            if not legal.can_fold:
                raise IllegalAction("cannot fold now")
            seat.folded = True
            seat.last_action = "fold"
            self._log("fold", seat.index, f"{seat.name} folds", street=self.street)

        elif act is Action.CHECK:
            if not legal.can_check:
                raise IllegalAction("cannot check, there is a bet to you")
            seat.last_action = "check"
            self._log("check", seat.index, f"{seat.name} checks", street=self.street)

        elif act is Action.CALL:
            if not legal.can_call:
                raise IllegalAction("nothing to call")
            paid = self._take(seat, legal.call_amount, Action.CALL, "call")
            seat.last_action = "call all-in" if seat.all_in else "call"
            if seat.all_in:
                self._log(
                    "call",
                    seat.index,
                    f"{seat.name} calls {paid:,} and is all in",
                    amount=paid,
                    street=self.street,
                )
            else:
                self._log("call", seat.index, f"{seat.name} calls {paid:,}", amount=paid, street=self.street)

        elif act is Action.BET:
            if not legal.can_bet:
                raise IllegalAction("cannot bet")
            if amount < legal.min_bet and amount != legal.max_raise_to:
                raise IllegalAction(
                    f"bet must be at least {legal.min_bet:,} (or all in for {legal.max_raise_to:,})"
                )
            if amount > legal.max_raise_to:
                raise IllegalAction("bet exceeds stack")
            self._commit_aggressive(seat, amount, is_raise=False)

        elif act is Action.RAISE:
            if not legal.can_raise:
                raise IllegalAction("cannot raise")
            if amount < legal.min_raise_to and amount != legal.max_raise_to:
                raise IllegalAction(
                    f"raise must be to at least {legal.min_raise_to:,} "
                    f"(or all in for {legal.max_raise_to:,})"
                )
            if amount > legal.max_raise_to:
                raise IllegalAction("raise exceeds stack")
            self._commit_aggressive(seat, amount, is_raise=True)

        else:
            raise IllegalAction(f"unsupported action {act}")

        # Only mark the action as taken once it has actually been applied, so a
        # rejected action cannot corrupt the round state. A player who has acted
        # may not raise again unless a full raise behind them reopens betting.
        seat.has_acted = True
        seat.can_raise = False
        self._advance_from(self.to_act)

    def _commit_aggressive(self, seat: Seat, target: int, is_raise: bool) -> None:
        """Put chips in to reach ``target`` total commitment on this street."""
        previous_bet = self.current_bet
        delta = target - seat.committed_street
        paid = self._take(seat, delta, Action.RAISE if is_raise else Action.BET, "raise")
        new_bet = seat.committed_street

        if is_raise:
            raise_size = new_bet - previous_bet
            full = raise_size >= self.last_full_raise
        else:
            raise_size = new_bet
            full = True

        self.current_bet = max(self.current_bet, new_bet)
        if full:
            self.last_full_raise = max(raise_size, self.big_blind)
            for other in self.contenders:
                other.can_raise = True
            for other in self.seats:
                if other.index != seat.index:
                    other.has_acted = False
        seat.has_acted = True
        seat.can_raise = False

        if seat.all_in:
            verb = "raises to" if is_raise else "bets"
            self._log(
                "all_in",
                seat.index,
                f"{seat.name} is all in for {new_bet:,}",
                amount=paid,
                street=self.street,
            )
        else:
            verb = "raises to" if is_raise else "bets"
            text = (
                f"{seat.name} {verb} {new_bet:,}"
                if is_raise
                else f"{seat.name} {verb} {new_bet:,}"
            )
            self._log("raise" if is_raise else "bet", seat.index, text, amount=paid, street=self.street)
        seat.last_action = f"{'raise' if is_raise else 'bet'} {new_bet:,}"

        if not full:
            # An all-in for less than a full raise does not reopen the betting.
            self._log(
                "incomplete_raise",
                seat.index,
                f"{seat.name}'s all-in is less than a full raise; betting not reopened",
                street=self.street,
            )

    # ------------------------------------------------------------------
    # progression
    # ------------------------------------------------------------------

    def _needs_action(self, seat: Seat) -> bool:
        if seat.folded or seat.sitting_out or seat.stack == 0:
            return False
        if seat.committed_street < self.current_bet:
            return True
        return not seat.has_acted

    def _next_to_act(self, after: int | None) -> int | None:
        """First seat clockwise after ``after`` that still needs to act."""
        if after is None:
            return None
        for step in range(1, self.n + 1):
            idx = (after + step) % self.n
            if self._needs_action(self.seats[idx]):
                return idx
        return None

    def _advance_from(self, acted: int | None) -> None:
        if acted is None:
            acted = self._bb_seat
        while True:
            if self.street in (Street.COMPLETE, Street.SHOWDOWN):
                return
            live = self.contenders
            if len(live) <= 1:
                self._finish(showdown=False)
                return

            nxt = self._next_to_act(acted)
            if nxt is None:
                # Nobody owes anything and everyone has acted: the street is done.
                if self.street is Street.RIVER:
                    self._finish(showdown=True)
                    return
                self._end_street()
                # After a street change the action restarts behind the button.
                acted = self.button
                continue

            # It only makes sense for a player to act if they owe chips, or if
            # someone else is still able to respond. Otherwise the remaining
            # cards are simply dealt out.
            owes = self.seats[nxt].committed_street < self.current_bet
            bettors = sum(1 for seat in live if seat.stack > 0)
            if owes or bettors >= 2:
                self.to_act = nxt
                return

            self._log(
                "all_in_runout",
                None,
                "Only one player can still bet; running the board out",
                street=self.street,
            )
            if self.street is not Street.RIVER:
                self._run_out_board()
            self._finish(showdown=True)
            return

    def _end_street(self) -> None:
        """Sweep the street's bets into the pot and deal the next cards."""
        self.pot += self.street_pot
        for seat in self.seats:
            seat.committed_street = 0
            seat.has_acted = False
            seat.can_raise = True
        self.current_bet = 0
        self.last_full_raise = self.big_blind
        self.min_raise_to = self.big_blind

        order = [Street.PREFLOP, Street.FLOP, Street.TURN, Street.RIVER]
        nxt = order[order.index(self.street) + 1]
        self.street = nxt
        count = BOARD_COUNTS[nxt]
        C.deal(self.deck, 1)  # burn
        self.board.extend(C.deal(self.deck, count - len(self.board)))
        self._log(
            "street",
            None,
            f"{nxt.value.title()}: {C.cards_glyphs(self.board)}",
            street=nxt,
        )
        self.to_act = None

    def _run_out_board(self) -> None:
        """Deal every remaining street without betting."""
        while self.street is not Street.RIVER:
            self._end_street()
        self.pot += self.street_pot
        for seat in self.seats:
            seat.committed_street = 0

    # ------------------------------------------------------------------
    # showdown
    # ------------------------------------------------------------------

    def build_pots(self) -> list[Pot]:
        """Split contributions into a main pot and any side pots.

        Uncalled bets return automatically: the top level of an over-bet ends up
        with a single eligible player, so it is awarded straight back.
        """
        contribs = [s.committed_total for s in self.seats]
        folded = [s.folded for s in self.seats]
        levels = sorted({c for c in contribs if c > 0})
        pots: list[Pot] = []
        previous = 0
        for level in levels:
            amount = sum(min(c, level) - min(c, previous) for c in contribs)
            eligible = [
                s.index
                for s in self.seats
                if contribs[s.index] >= level and not folded[s.index]
            ]
            if amount <= 0:
                previous = level
                continue
            if not eligible:
                # Nothing can be won from this slice; fold it into the last pot.
                if pots:
                    pots[-1].amount += amount
                else:
                    pots.append(Pot(amount, [s.index for s in self.seats if not folded[s.index]]))
                previous = level
                continue
            label = "main" if not pots else f"side {len(pots)}"
            pots.append(Pot(amount, eligible, label))
            previous = level
        return pots

    def _finish(self, showdown: bool) -> None:
        self.to_act = None
        self.pot += self.street_pot
        for seat in self.seats:
            seat.committed_street = 0

        result = HandResult(showdown=showdown)
        pots = self.build_pots()

        if not showdown:
            winner = self.contenders[0]
            total = sum(p.amount for p in pots)
            result.payouts.append(Payout(winner.index, total, "main"))
            self._log(
                "win_uncontested",
                winner.index,
                f"{winner.name} wins {total:,} (everyone folded)",
                amount=total,
                street=self.street,
            )
        else:
            self.street = Street.SHOWDOWN
            scores: dict[int, tuple[int, ...]] = {}
            for seat in self.contenders:
                full = tuple(seat.hole) + tuple(self.board)
                scores[seat.index] = E.evaluate(full)
                result.revealed[seat.index] = list(seat.hole)
                result.went_to_showdown_hands.append(seat.index)

            for pot in pots:
                live = [i for i in pot.eligible if i in scores]
                if not live:
                    continue
                best = max(scores[i] for i in live)
                winners = [i for i in live if scores[i] == best]
                if result.winning_score is None or best > result.winning_score:
                    result.winning_score = best
                share, remainder = divmod(pot.amount, len(winners))
                ordered = self._clockwise_from_button(winners)
                for position, seat_index in enumerate(ordered):
                    amount = share + (1 if position < remainder else 0)
                    result.payouts.append(
                        Payout(seat_index, amount, pot.label, scores[seat_index])
                    )
                    self._log(
                        "pot_award",
                        seat_index,
                        f"{self.seats[seat_index].name} wins {pot.label} "
                        f"pot of {pot.amount:,} with {E.hand_name(scores[seat_index])}",
                        amount=amount,
                        street=Street.SHOWDOWN,
                        meta={"pot": pot.label, "hand": E.hand_name(scores[seat_index])},
                    )
            self._log(
                "showdown",
                None,
                "Showdown: "
                + ", ".join(
                    f"{self.seats[i].name} {C.cards_glyphs(self.seats[i].hole)}"
                    for i in sorted(scores)
                ),
                street=Street.SHOWDOWN,
                meta={"board": C.cards_glyphs(self.board)},
            )

        for payout in result.payouts:
            self.seats[payout.seat].stack += payout.amount

        # Every chip is back in a stack now, so the pot must be empty. Leaving
        # it populated is the classic way to invent chips out of nothing.
        self.pot = 0
        self.result = result
        self.street = Street.COMPLETE

    def _clockwise_from_button(self, indices: Sequence[int]) -> list[int]:
        """Order seats starting with the first seat left of the button."""
        wanted = set(indices)
        ordered: list[int] = []
        for step in range(1, self.n + 1):
            idx = (self.button + step) % self.n
            if idx in wanted:
                ordered.append(idx)
        return ordered

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _log(
        self,
        kind: str,
        seat: int | None,
        text: str,
        amount: int = 0,
        street: Street | None = None,
        meta: dict | None = None,
    ) -> None:
        self.events.append(
            HandEvent(kind, seat, text, amount, street or self.street, meta or {})
        )

    def reveal_seed(self) -> dict[str, str]:
        """Commit-reveal disclosure, for clients to verify the deal."""
        return {
            "commit": self.deck_commit,
            "seed": self.seed.hex(),
            "deck": C.deck_key(self.original_deck),
        }

    def public_state(self, viewer: int | None = None) -> dict:
        """Everything safe to broadcast, with hole cards only for ``viewer``."""
        seats = []
        for seat in self.seats:
            show_hole = (
                viewer is not None and seat.index == viewer
            ) or (
                self.result is not None and seat.index in self.result.revealed
            )
            seats.append(
                {
                    "index": seat.index,
                    "name": seat.name,
                    "stack": seat.stack,
                    "committed": seat.committed_street,
                    "folded": seat.folded,
                    "all_in": seat.all_in,
                    "sitting_out": seat.sitting_out,
                    "is_bot": seat.is_bot,
                    "last_action": seat.last_action,
                    "hole": [C.card_str(c) for c in seat.hole] if show_hole else [],
                    "hole_hidden": bool(seat.hole) and not show_hole,
                }
            )
        return {
            "hand_id": self.hand_id,
            "street": self.street.value,
            "board": [C.card_str(c) for c in self.board],
            "pot": self.total_pot,
            "current_bet": self.current_bet,
            "to_act": self.to_act,
            "button": self.button,
            "small_blind": self.small_blind,
            "big_blind": self.big_blind,
            "seats": seats,
            "complete": self.street is Street.COMPLETE,
        }


class Table:
    """A running table: seats, blinds, button and a sequence of hands."""

    def __init__(
        self,
        seats: Sequence[Seat],
        small_blind: int,
        big_blind: int,
        button: int = 0,
        ante: int = 0,
    ) -> None:
        self.seats = list(seats)
        self.small_blind = small_blind
        self.big_blind = big_blind
        self.ante = ante
        self.button = button % len(self.seats)
        self.hand_number = 0
        self.hand: Hand | None = None
        self.history: list[Hand] = []

    def start_hand(self, deck: Sequence[int] | None = None, seed: bytes | None = None) -> Hand:
        self.hand_number += 1
        self.hand = Hand(
            self.seats,
            self.button,
            self.small_blind,
            self.big_blind,
            deck=deck,
            seed=seed,
            hand_id=self.hand_number,
            ante=self.ante,
        )
        self.hand.start()
        return self.hand

    def finish_hand(self) -> None:
        if self.hand is not None:
            self.history.append(self.hand)

    def advance_button(self) -> None:
        """Move the button to the next seat that is still in the game."""
        for step in range(1, len(self.seats) + 1):
            idx = (self.button + step) % len(self.seats)
            if self.seats[idx].stack > 0 and not self.seats[idx].sitting_out:
                self.button = idx
                return

    def live_seats(self) -> list[Seat]:
        return [s for s in self.seats if s.stack > 0 and not s.sitting_out]

    def chips_in_play(self) -> int:
        total = sum(s.stack for s in self.seats)
        if self.hand is not None and self.hand.street is not Street.COMPLETE:
            total += self.hand.total_pot
        return total
