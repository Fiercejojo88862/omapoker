"""Card representation.

A card is an ``int`` in ``0..51`` laid out rank-major so that comparisons and
bit tricks are cheap:

    rank = card // 4     0 = deuce ... 12 = ace
    suit = card % 4      0 = clubs, 1 = diamonds, 2 = hearts, 3 = spades

Plain ints (rather than objects) keep the Monte-Carlo equity loops fast enough
to run inside a Textual UI without a worker farm.
"""

from __future__ import annotations

import hashlib
import os
import random
from typing import Iterable, Iterator, Sequence

RANK_SYMBOLS = "23456789TJQKA"
SUIT_SYMBOLS = "cdhs"
SUIT_GLYPHS = ("\u2663", "\u2666", "\u2665", "\u2660")  # club diamond heart spade

NUM_CARDS = 52
RANKS: tuple[int, ...] = tuple(range(13))
SUITS: tuple[int, ...] = tuple(range(4))
FULL_DECK: tuple[int, ...] = tuple(range(NUM_CARDS))


def card(rank: int, suit: int) -> int:
    """Build a card from a rank (0-12) and suit (0-3)."""
    if not 0 <= rank <= 12:
        raise ValueError(f"rank out of range: {rank}")
    if not 0 <= suit <= 3:
        raise ValueError(f"suit out of range: {suit}")
    return rank * 4 + suit


def rank_of(c: int) -> int:
    return c >> 2


def suit_of(c: int) -> int:
    return c & 3


def parse_card(text: str) -> int:
    """Parse ``"As"``, ``"10h"``, ``"td"`` into a card int."""
    text = text.strip()
    if not text:
        raise ValueError("empty card string")
    rank_char = text[0].upper()
    suit_part = text[1:]
    if rank_char == "1" and suit_part[:1] == "0":
        rank_char, suit_part = "T", suit_part[1:]
    if rank_char not in RANK_SYMBOLS:
        raise ValueError(f"bad rank in {text!r}")
    if not suit_part:
        raise ValueError(f"missing suit in {text!r}")
    suit_char = suit_part[0].lower()
    if suit_char not in SUIT_SYMBOLS:
        raise ValueError(f"bad suit in {text!r}")
    return card(RANK_SYMBOLS.index(rank_char), SUIT_SYMBOLS.index(suit_char))


def parse_cards(text: str) -> list[int]:
    """Parse a whitespace- or comma-separated run of cards."""
    cleaned = text.replace(",", " ").split()
    cards = [parse_card(tok) for tok in cleaned]
    _assert_unique(cards)
    return cards


def card_str(c: int) -> str:
    return f"{RANK_SYMBOLS[rank_of(c)]}{SUIT_SYMBOLS[suit_of(c)]}"


def card_glyph(c: int) -> str:
    """Unicode presentation, e.g. ``A♠``."""
    return f"{RANK_SYMBOLS[rank_of(c)]}{SUIT_GLYPHS[suit_of(c)]}"


def cards_str(cards: Iterable[int], sep: str = " ") -> str:
    return sep.join(card_str(c) for c in cards)


def cards_glyphs(cards: Iterable[int], sep: str = " ") -> str:
    return sep.join(card_glyph(c) for c in cards)


def _assert_unique(cards: Sequence[int]) -> None:
    if len(set(cards)) != len(cards):
        raise ValueError(f"duplicate cards: {[card_str(c) for c in cards]}")


def new_deck(rng: random.Random | None = None) -> list[int]:
    """A shuffled 52-card deck (Fisher-Yates)."""
    deck = list(FULL_DECK)
    shuffle(deck, rng)
    return deck


def shuffle(deck: list[int], rng: random.Random | None = None) -> list[int]:
    """In-place Fisher-Yates, using ``os.urandom`` seeding by default."""
    rng = rng if rng is not None else random.Random(os.urandom(32))
    for i in range(len(deck) - 1, 0, -1):
        j = rng.randrange(i + 1)
        deck[i], deck[j] = deck[j], deck[i]
    return deck


def deal(deck: list[int], count: int) -> list[int]:
    if count > len(deck):
        raise ValueError(f"cannot deal {count} from {len(deck)} cards")
    dealt = deck[:count]
    del deck[:count]
    return dealt


def remove(deck: list[int], used: Iterable[int]) -> list[int]:
    """Return ``deck`` without ``used`` (order preserved)."""
    blocked = set(used)
    return [c for c in deck if c not in blocked]


def deck_key(deck: Sequence[int]) -> str:
    """A compact string form of a whole deck, for commit-reveal hashing."""
    return "".join(f"{c:02x}" for c in deck)


def deck_from_key(key: str) -> list[int]:
    if len(key) % 2:
        raise ValueError("deck key must have an even length")
    return [int(key[i : i + 2], 16) for i in range(0, len(key), 2)]


# --------------------------------------------------------------------------
# provable shuffles
# --------------------------------------------------------------------------
#
# A hand is dealt from a deck shuffled with a 32-byte seed. Publishing
# ``sha256(deck)`` before the cards come out and the seed afterwards lets anyone
# at the table check that the deck was fixed in advance and not doctored once
# the hole cards were known. The dealer can still have picked the seed to suit
# themselves - this proves the deal was *not changed mid-hand*, which is what
# matters when the dealer is another player's machine.

def deck_from_seed(seed: bytes) -> list[int]:
    """Rebuild the deck a seed produces. Matches :class:`Hand` exactly."""
    deck = list(FULL_DECK)
    shuffle(deck, random.Random(seed))
    return deck


def commit_for_deck(deck: Sequence[int]) -> str:
    return hashlib.sha256(deck_key(deck).encode()).hexdigest()


def verify_deck(seed: bytes, commit: str) -> bool:
    """True when ``seed`` really produces the deck that was committed to."""
    try:
        return commit_for_deck(deck_from_seed(seed)) == commit
    except Exception:
        return False


def rank_histogram(cards: Iterable[int]) -> dict[int, int]:
    hist: dict[int, int] = {}
    for c in cards:
        r = rank_of(c)
        hist[r] = hist.get(r, 0) + 1
    return hist


def suit_histogram(cards: Iterable[int]) -> dict[int, int]:
    hist: dict[int, int] = {}
    for c in cards:
        s = suit_of(c)
        hist[s] = hist.get(s, 0) + 1
    return hist


def iter_deck(exclude: Iterable[int] = ()) -> Iterator[int]:
    blocked = set(exclude)
    for c in FULL_DECK:
        if c not in blocked:
            yield c
