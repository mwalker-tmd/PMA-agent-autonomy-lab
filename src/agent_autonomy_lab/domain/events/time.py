"""UTC normalization for event timestamps."""

from datetime import UTC, datetime, tzinfo


def to_utc(value: str | datetime, source_tz: tzinfo) -> datetime:
    """Return ``value`` as a timezone-aware UTC datetime.

    τ-bench writes local time with no zone (``2026-10-27T00:25:09``), so a
    zone-less value is read as ``source_tz``, the zone of the machine that ran
    the simulation. A value that already has a zone is converted, and
    ``source_tz`` is ignored. During a daylight-saving fall-back hour a
    zone-less time is ambiguous; the earlier instant is used.
    """
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=source_tz)
    return parsed.astimezone(UTC)
