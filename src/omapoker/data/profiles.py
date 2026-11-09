"""Player identity: profiles and verifiable identity codes.

An identity code looks like ``OMA-ID-4H7X2NQ9-3K7MPW2A``. It is a fingerprint of
an Ed25519 public key, so the code is short and human-typable while remaining
bound to a key that can actually sign.

Verification works like this:

1. A client claiming an identity sends its public key and its code.
2. The host recomputes the code from the public key, so a code cannot be paired
   with someone else's key.
3. The host sends a random nonce; the client signs it; the host verifies the
   signature against the public key.

No secret ever travels. What this proves is *the same key holder came back*,
which is trust-on-first-use, not a certificate authority. If the ``cryptography``
package is unavailable, identities still work as labels and the UI says plainly
that they are unverified.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import asdict, dataclass, field

#: Crockford-style alphabet: no I, L, O or U to avoid transcription confusion.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CODE_PREFIX = "OMA-ID"

try:  # pragma: no cover - exercised by the availability test
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )

    CRYPTO_AVAILABLE = True
except Exception:  # pragma: no cover
    CRYPTO_AVAILABLE = False


def crypto_available() -> bool:
    return CRYPTO_AVAILABLE


def _base32(data: bytes) -> str:
    """Crockford base32 without padding."""
    bits = int.from_bytes(data, "big")
    length = (len(data) * 8 + 4) // 5
    digits = []
    for _ in range(length):
        digits.append(_ALPHABET[bits & 0x1F])
        bits >>= 5
    return "".join(reversed(digits))


def public_key_from_private(private_hex: str) -> str:
    if not CRYPTO_AVAILABLE:
        raise RuntimeError("the cryptography package is required for key derivation")
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_hex))
    from cryptography.hazmat.primitives import serialization

    raw = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return raw.hex()


def code_for_public_key(public_hex: str) -> str:
    """The human-typable code for a public key."""
    digest = hashlib.sha256(bytes.fromhex(public_hex)).digest()
    text = _base32(digest[:10])  # 80 bits -> exactly 16 characters
    return f"{_CODE_PREFIX}-{text[:8]}-{text[8:16]}"


def normalise_code(code: str) -> str:
    """Accept a code typed with spaces, lowercase or missing hyphens.

    The prefix must be removed *before* filtering to the code alphabet: the
    alphabet deliberately omits O, I, L and U, so filtering first would eat the
    letters of "OMAID" itself.
    """
    cleaned = code.upper().replace(" ", "").replace("-", "").replace("_", "")
    if cleaned.startswith("OMAID"):
        cleaned = cleaned[5:]
    cleaned = "".join(c for c in cleaned if c in _ALPHABET)[:16]
    if len(cleaned) < 16:
        return code.upper().strip()
    return f"{_CODE_PREFIX}-{cleaned[:8]}-{cleaned[8:16]}"


@dataclass
class Identity:
    """A profile's key material and derived code."""

    public_hex: str = ""
    private_hex: str = ""
    verified: bool = False  # False when cryptography is missing

    @classmethod
    def generate(cls) -> "Identity":
        if CRYPTO_AVAILABLE:
            private = Ed25519PrivateKey.generate()
            from cryptography.hazmat.primitives import serialization

            private_bytes = private.private_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption(),
            )
            public_bytes = private.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            return cls(
                public_hex=public_bytes.hex(),
                private_hex=private_bytes.hex(),
                verified=True,
            )
        # Fallback: a random label with no key material.
        return cls(public_hex=os.urandom(32).hex(), private_hex="", verified=False)

    @property
    def code(self) -> str:
        return code_for_public_key(self.public_hex)

    def sign(self, nonce: bytes) -> str:
        """Sign a challenge. Returns "" when no key material is available."""
        if not (CRYPTO_AVAILABLE and self.private_hex):
            return ""
        key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(self.private_hex))
        return key.sign(nonce).hex()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Identity":
        return cls(
            public_hex=data.get("public_hex", ""),
            private_hex=data.get("private_hex", ""),
            verified=bool(data.get("verified", False)),
        )


def verify_signature(public_hex: str, nonce: bytes, signature_hex: str) -> bool:
    """Check a challenge response. Never raises."""
    if not CRYPTO_AVAILABLE:
        return False
    try:
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_hex))
        key.verify(bytes.fromhex(signature_hex), nonce)
        return True
    except Exception:
        return False


def code_matches_key(code: str, public_hex: str) -> bool:
    """Confirm a code really belongs to a public key."""
    try:
        return normalise_code(code) == code_for_public_key(public_hex)
    except Exception:
        return False


# --------------------------------------------------------------------------
# profiles
# --------------------------------------------------------------------------

EXPERIENCE_LEVELS = ("newcomer", "casual", "skilled", "advanced")

#: What each experience level pre-selects on the assist dial.
EXPERIENCE_ASSIST = {
    "newcomer": 5,
    "casual": 4,
    "skilled": 3,
    "advanced": 1,
}

EXPERIENCE_LABELS = {
    "newcomer": "Newcomer - I am still learning the rules",
    "casual": "Casual - I know the rules, I want to improve",
    "skilled": "Skilled - I play regularly",
    "advanced": "Advanced - just tell me the numbers",
}


@dataclass
class Profile:
    """A local player profile."""

    id: str
    name: str
    created: str = ""
    experience: str = "casual"
    assist_level: int = 3
    cards_style: str = "glyphs"          # glyphs | ascii
    sound: bool = False
    music_source: str = "off"
    music_volume: int = 60
    music_folders: list[str] = field(default_factory=list)
    music_resume: bool = False
    identity: Identity = field(default_factory=Identity)
    last_seen: str = ""

    @classmethod
    def new(cls, profile_id: str, name: str, experience: str = "casual") -> "Profile":
        from datetime import datetime

        stamp = datetime.now().isoformat(timespec="seconds")
        return cls(
            id=profile_id,
            name=name,
            created=stamp,
            last_seen=stamp,
            experience=experience,
            assist_level=EXPERIENCE_ASSIST.get(experience, 3),
            identity=Identity.generate(),
        )

    def to_dict(self) -> dict:
        data = asdict(self)
        data["identity"] = self.identity.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Profile":
        known = {
            "id",
            "name",
            "created",
            "experience",
            "assist_level",
            "cards_style",
            "sound",
            "music_source",
            "music_volume",
            "music_folders",
            "music_resume",
            "last_seen",
        }
        kwargs = {k: v for k, v in data.items() if k in known}
        profile = cls(**kwargs)
        profile.identity = Identity.from_dict(data.get("identity") or {})
        return profile
