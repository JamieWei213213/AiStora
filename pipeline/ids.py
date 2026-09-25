"""ULIDs for load identifiers.

A ULID sorts by creation time, so ``manifests/.../`` listings come back in
load order and a load id doubles as a timestamp when debugging. The 80 random
bits make collisions between two uploads in the same millisecond a
non-issue. No third-party package is needed for 20 lines.
"""

from __future__ import annotations

import os
import re
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32
_ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


def new_ulid(timestamp_ms: int | None = None) -> str:
    if timestamp_ms is None:
        timestamp_ms = int(time.time() * 1000)
    value = (timestamp_ms << 80) | int.from_bytes(os.urandom(10), "big")
    chars = []
    for _ in range(26):
        chars.append(_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def is_ulid(value: str) -> bool:
    return bool(_ULID.match(str(value or "")))


def ulid_timestamp_ms(value: str) -> int:
    """Milliseconds since the epoch encoded in the first ten characters."""
    if not is_ulid(value):
        raise ValueError("not a ULID")
    number = 0
    for char in value[:10]:
        number = (number << 5) | _ALPHABET.index(char)
    return number
