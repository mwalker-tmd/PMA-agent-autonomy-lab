"""Reference writes: the comparable form, the port's double, and gold replay on real tasks."""

import pytest
from tau2.domains.retail.environment import get_tasks

from agent_autonomy_lab.adapters.tau2.reference_writes import GoldReplayReferenceWrites
from agent_autonomy_lab.domain.reference_writes import (
    DEFECT_TASKS,
    ReferenceWrites,
    WriteCall,
    is_multi_item_modification,
    normalized_write,
)
from agent_autonomy_lab.domain.tool_timeline import WRITE_TOOLS
from agent_autonomy_lab.ports.reference_writes import CannedReferenceWrites

EXCHANGE = "exchange_delivered_order_items"
MODIFY = "modify_pending_order_items"
RETURN = "return_delivered_order_items"


@pytest.fixture(scope="module")
def source() -> GoldReplayReferenceWrites:
    return GoldReplayReferenceWrites()


def test_item_order_does_not_change_a_return():
    first = normalized_write(RETURN, {"order_id": "#1", "item_ids": ["a", "b"]})
    assert first == normalized_write(RETURN, {"order_id": "#1", "item_ids": ["b", "a"]})


@pytest.mark.parametrize("tool", [EXCHANGE, MODIFY])
def test_pairs_stay_pairs(tool):
    pairs = {"item_ids": ["a", "b"], "new_item_ids": ["c", "d"]}
    reordered = {"item_ids": ["b", "a"], "new_item_ids": ["d", "c"]}
    crossed = {"item_ids": ["a", "b"], "new_item_ids": ["d", "c"]}
    assert normalized_write(tool, pairs) == normalized_write(tool, reordered)
    assert normalized_write(tool, pairs) != normalized_write(tool, crossed)


def test_repeated_items_stay_repeated():
    once = normalized_write(RETURN, {"item_ids": ["a"]})
    assert once != normalized_write(RETURN, {"item_ids": ["a", "a"]})


def test_other_tools_and_arguments_are_compared_exactly():
    assert normalized_write("cancel_pending_order", {"reason": "a"}) != normalized_write(
        "cancel_pending_order", {"reason": "b"}
    )
    assert normalized_write("modify_user_address", {"a": ["x", "y"]}) != normalized_write(
        "modify_user_address", {"a": ["y", "x"]}
    )


@pytest.mark.parametrize(
    "arguments",
    [{"item_ids": ["a"], "new_item_ids": ["b", "c"]}, {"item_ids": "a", "new_item_ids": ["b"]}],
)
def test_malformed_item_lists_stay_as_they_are(arguments):
    assert normalized_write(EXCHANGE, arguments) == normalized_write(EXCHANGE, dict(arguments))
    assert '"item_pairs"' not in normalized_write(EXCHANGE, arguments)


def test_only_a_multi_item_modification_depends_on_item_order():
    assert is_multi_item_modification(MODIFY, {"item_ids": ["a", "b"]})
    assert not is_multi_item_modification(MODIFY, {"item_ids": ["a"]})
    assert not is_multi_item_modification(EXCHANGE, {"item_ids": ["a", "b"]})


def test_the_multiset_counts_repeats():
    write = WriteCall("cancel_pending_order", {"order_id": "#1"})
    assert ReferenceWrites("1", (write, write)).multiset[write.normalized] == 2


def test_the_double_returns_none_for_a_task_it_was_not_given():
    double = CannedReferenceWrites({"1": ReferenceWrites("1")})
    assert double.reference_writes("1") == ReferenceWrites("1")
    assert double.reference_writes("2") is None


# Real tasks at the pinned commit (docs/BASELINE.md, "Tool correctness")


def test_a_task_the_benchmark_lacks_has_no_reference(source):
    assert source.reference_writes("not-a-task") is None


def test_only_the_two_recorded_reference_writes_fail_on_gold_replay(source):
    tasks = get_tasks(task_split_name=None)
    dropped = {
        task.id: [w.name for w in source.reference_writes(task.id).dropped]
        for task in tasks
        if source.reference_writes(task.id).dropped
    }
    assert len(tasks) == 114
    assert dropped == {
        "64": ["exchange_delivered_order_items"],
        "105": ["exchange_delivered_order_items"],
    }


def test_task_64_expects_only_the_modification_that_ran(source):
    reference = source.reference_writes("64")
    assert [write.name for write in reference.expected] == [MODIFY]


def test_task_105_expects_no_write(source):
    assert source.reference_writes("105").expected == ()


def test_expected_writes_are_only_writes(source):
    for task in get_tasks(task_split_name=None):
        assert {w.name for w in source.reference_writes(task.id).expected} <= WRITE_TOOLS


def test_a_read_only_task_expects_no_write(source):
    assert source.reference_writes("68").expected == ()


def test_task_22_expects_the_address_written_and_restored(source):
    names = [write.name for write in source.reference_writes("22").expected]
    assert names.count("modify_user_address") == 2


def test_every_multi_item_modification_in_a_reference_is_a_defect_task(source):
    """So AA-5's exception cannot arise from a reference inside the Milestone 4 task sets."""
    multi = {
        task.id
        for task in get_tasks(task_split_name=None)
        if any(
            is_multi_item_modification(w.name, w.arguments)
            for w in source.reference_writes(task.id).expected
        )
    }
    assert multi == {"20", "21", "36", "37", "100"}
    assert multi < DEFECT_TASKS


def test_a_task_is_replayed_once(source):
    assert source.reference_writes("0") is source.reference_writes("0")
