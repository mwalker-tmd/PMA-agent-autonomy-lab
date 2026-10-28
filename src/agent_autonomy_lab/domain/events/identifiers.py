"""Identifier generation for trace events.

``new_id`` returns a time-ordered UUID version 7 (RFC 9562), the form LangSmith
uses for run IDs, so events sort by creation time. ``stable_id`` returns the
same UUID version 5 for the same inputs, so re-exporting a saved simulation
reuses its thread instead of creating a duplicate.
"""

import os
import time
import uuid

# Namespace for the lab's name-based UUIDs. Changing it changes every stable ID.
LAB_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL, "https://github.com/mwalker-tmd/PMA-agent-autonomy-lab"
)


def new_id(now_ms: int | None = None, random_bytes: bytes | None = None) -> uuid.UUID:
    """Return a new UUID version 7.

    The first 48 bits are the Unix time in milliseconds; the rest is random
    apart from the version and variant bits. ``now_ms`` and ``random_bytes``
    (10 bytes) exist for tests.
    """
    if now_ms is None:
        now_ms = time.time_ns() // 1_000_000
    if not 0 <= now_ms < 1 << 48:
        raise ValueError("now_ms must fit in 48 bits")
    if random_bytes is None:
        random_bytes = os.urandom(10)
    if len(random_bytes) != 10:
        raise ValueError("random_bytes must be 10 bytes")
    rand = int.from_bytes(random_bytes)
    rand_a = rand >> 68  # 12 bits
    rand_b = rand & ((1 << 62) - 1)  # 62 bits
    value = (now_ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return uuid.UUID(int=value)


def stable_id(*parts: str) -> uuid.UUID:
    """Return a UUID version 5 derived from ``parts`` under the lab namespace."""
    if not parts or any(not part for part in parts):
        raise ValueError("stable_id needs at least one non-empty part")
    return uuid.uuid5(LAB_NAMESPACE, "\x1f".join(parts))


def thread_id_for(simulation_id: str) -> str:
    """Return the thread ID for a simulation: one τ-bench simulation is one thread."""
    return str(stable_id("thread", simulation_id))
