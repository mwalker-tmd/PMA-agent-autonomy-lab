"""The point-in-time state records and the port's test double."""

from datetime import UTC, datetime

import pytest

from agent_autonomy_lab.domain.retail_state import (
    ReplayDivergence,
    RetailState,
    RetailStateTimeline,
    WriteAttempt,
    WriteOutcome,
)
from agent_autonomy_lab.domain.simulations import SavedSimulation
from agent_autonomy_lab.ports.retail_state import CannedRetailState


def state(db_hash: str = "hash") -> RetailState:
    return RetailState(db_hash=db_hash, orders={}, users={}, products={})


def write(call_id: str = "call-1", before: RetailState | None = None) -> WriteAttempt:
    return WriteAttempt(call_id, "cancel_pending_order", WriteOutcome.EXECUTED, before)


def test_a_state_is_a_detached_copy():
    orders = {"#W1": {"status": "pending"}}
    copied = RetailState(db_hash="hash", orders=orders, users={}, products={})
    orders["#W1"]["status"] = "cancelled"
    assert copied.orders["#W1"]["status"] == "pending"


def test_a_state_needs_a_hash_and_json_data():
    with pytest.raises(ValueError, match="db_hash"):
        state("")
    with pytest.raises(TypeError):
        RetailState(db_hash="hash", orders={"#W1": object()}, users={}, products={})


def test_before_returns_the_state_of_that_write():
    first, second = state("first"), state("second")
    timeline = RetailStateTimeline(
        "sim",
        (write("call-1", first), write("call-2", second)),
        final=state("end"),
        replayed_hash="end",
    )
    assert timeline.before("call-2") is second


@pytest.mark.parametrize("call_id", ["unknown", "repeated"])
def test_before_gives_nothing_for_an_unknown_or_ambiguous_write(call_id):
    timeline = RetailStateTimeline(
        "sim",
        (write("repeated", state()), write("repeated", state())),
        final=state("end"),
        replayed_hash="end",
    )
    assert timeline.before(call_id) is None


def test_final_is_withheld_exactly_when_the_replay_diverged():
    divergence = ReplayDivergence("call-1", "cancel_pending_order", "differs")
    with pytest.raises(ValueError, match="diverged"):
        RetailStateTimeline(
            "sim", (), final=state("end"), replayed_hash="end", divergences=(divergence,)
        )
    with pytest.raises(ValueError, match="diverged"):
        RetailStateTimeline("sim", (), final=None, replayed_hash="end")
    kept = RetailStateTimeline(
        "sim", (), final=None, replayed_hash="end", divergences=(divergence,)
    )
    assert kept.replayed_hash == "end"


def test_final_must_match_the_replayed_hash():
    with pytest.raises(ValueError, match="replayed_hash"):
        RetailStateTimeline("sim", (), final=state("other"), replayed_hash="end")


def test_canned_state_returns_prepared_timelines_and_records_requests():
    timeline = RetailStateTimeline("sim", (), final=state("end"), replayed_hash="end")
    source = CannedRetailState({"sim": timeline})
    simulation = SavedSimulation("sim", "0", datetime(2026, 10, 31, tzinfo=UTC), 1.0, None, ())
    assert source.timeline(simulation) is timeline
    assert source.simulations == [simulation]
    other = SavedSimulation("other", "0", datetime(2026, 10, 31, tzinfo=UTC), 1.0, None, ())
    with pytest.raises(KeyError):
        source.timeline(other)
