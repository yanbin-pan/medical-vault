"""ULID generation.

ULIDs rather than UUIDs because they sort by creation time as plain strings.
That makes a directory listing of the vault chronological with no index, and
keeps B-tree inserts on the primary key sequential.
"""

from __future__ import annotations

import os
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32: no I, L, O, U
_ENCODED_LEN = 26


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def new_ulid(when_ms: int | None = None) -> str:
    """Return a fresh 26-character ULID: 48 bits of timestamp, 80 bits random."""
    ts = int(time.time() * 1000) if when_ms is None else when_ms
    randomness = int.from_bytes(os.urandom(10), "big")
    return _encode(ts, 10) + _encode(randomness, 16)


def ulid_timestamp_ms(ulid: str) -> int:
    """Recover the millisecond timestamp encoded in a ULID's first 10 characters."""
    value = 0
    for char in ulid[:10]:
        value = value * 32 + _ALPHABET.index(char)
    return value


def is_ulid(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _ENCODED_LEN
        and all(c in _ALPHABET for c in value)
    )
