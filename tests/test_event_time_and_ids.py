from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from agent_autonomy_lab.domain.events import new_id, stable_id, thread_id_for, to_utc

DENVER = ZoneInfo("America/Denver")


def test_zone_less_tau2_timestamp_is_read_as_source_zone():
    # Baseline run: 2026-10-27T00:25:09 local was 06:25Z (docs/BASELINE.md).
    assert to_utc("2026-10-27T00:25:09", DENVER) == datetime(2026, 10, 27, 6, 25, 9, tzinfo=UTC)


def test_zone_less_timestamp_keeps_microseconds():
    result = to_utc("2026-10-27T00:25:09.123456", DENVER)
    assert result.microsecond == 123456
    assert result.utcoffset() == timedelta(0)


def test_source_zone_daylight_saving_is_applied_per_date():
    # Same wall-clock time, one day after daylight saving ends (2026-11-01).
    assert to_utc("2026-11-02T00:25:09", DENVER).hour == 7


def test_ambiguous_fall_back_time_uses_earlier_instant():
    # 01:30 happens twice in Denver on 2026-11-01; the first is MDT (UTC-6).
    assert to_utc("2026-11-01T01:30:00", DENVER) == datetime(2026, 11, 1, 7, 30, tzinfo=UTC)


def test_aware_timestamp_is_converted_and_source_zone_ignored():
    aware = "2026-10-27T00:25:09+02:00"
    assert to_utc(aware, DENVER) == datetime(2026, 10, 26, 22, 25, 9, tzinfo=UTC)


def test_datetime_input_is_accepted():
    naive = datetime(2026, 10, 27, 0, 25, 9)
    assert to_utc(naive, timezone(timedelta(hours=-6))).hour == 6


def test_new_id_is_uuid7_with_timestamp_prefix():
    now_ms = 1_793_082_309_000
    uid = new_id(now_ms=now_ms, random_bytes=bytes(10))
    assert uid.version == 7
    assert uid.variant == "specified in RFC 4122"
    assert uid.int >> 80 == now_ms


def test_new_id_sorts_by_time():
    earlier = new_id(now_ms=1_000, random_bytes=b"\xff" * 10)
    later = new_id(now_ms=1_001, random_bytes=bytes(10))
    assert earlier < later


def test_new_id_is_unique():
    assert len({new_id() for _ in range(1000)}) == 1000


@pytest.mark.parametrize(
    "kwargs", [{"now_ms": -1}, {"now_ms": 1 << 48}, {"random_bytes": b"short"}]
)
def test_new_id_rejects_bad_inputs(kwargs):
    with pytest.raises(ValueError):
        new_id(**kwargs)


def test_stable_id_is_deterministic_and_part_sensitive():
    assert stable_id("thread", "sim-1") == stable_id("thread", "sim-1")
    assert stable_id("thread", "sim-1").version == 5
    assert stable_id("thread", "sim-1") != stable_id("thread", "sim-2")
    assert stable_id("a", "bc") != stable_id("ab", "c")


@pytest.mark.parametrize("parts", [(), ("thread", "")])
def test_stable_id_rejects_empty_parts(parts):
    with pytest.raises(ValueError):
        stable_id(*parts)


def test_thread_id_is_stable_per_simulation():
    sim = "2b7c6a3e-1f4d-4a8e-9b1c-0d2e3f4a5b6c"
    assert thread_id_for(sim) == thread_id_for(sim)
    assert thread_id_for(sim) != thread_id_for("another-simulation")
