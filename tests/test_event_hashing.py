import hashlib

import pytest

from agent_autonomy_lab.domain.events import (
    EventType,
    argument_hash,
    canonical_json,
    policy_version,
    tool_schema_version,
)
from tests.conftest import make_event

ARGS = {"order_id": "#W2378156", "reason": "no longer needed"}


def test_argument_hash_is_sha256_of_canonical_json():
    expected = hashlib.sha256(b'{"order_id":"#W2378156","reason":"no longer needed"}').hexdigest()
    assert argument_hash(ARGS) == f"sha256:{expected}"


def test_argument_hash_ignores_key_order():
    assert argument_hash({"reason": "no longer needed", "order_id": "#W2378156"}) == argument_hash(
        ARGS
    )


def test_argument_hash_changes_with_any_value():
    assert argument_hash({**ARGS, "order_id": "#W2378157"}) != argument_hash(ARGS)


def test_argument_hash_does_not_contain_the_arguments():
    assert "W2378156" not in argument_hash(ARGS)


def test_argument_hash_covers_nested_values():
    a = {"item_ids": ["1", "2"], "payment": {"id": "credit_card_1"}}
    b = {"payment": {"id": "credit_card_1"}, "item_ids": ["1", "2"]}
    assert argument_hash(a) == argument_hash(b)
    assert argument_hash(a) != argument_hash({**a, "item_ids": ["2", "1"]})


def test_canonical_json_rejects_non_json_values():
    with pytest.raises(TypeError):
        canonical_json({"when": object()})


def test_policy_version_is_prefix_of_sha256_of_policy_text():
    text = "# Retail agent policy\nAuthenticate the user first.\n"
    digest = hashlib.sha256(text.encode()).hexdigest()
    assert policy_version(text) == f"policy-{digest[:12]}"


def test_policy_version_ignores_line_ending_style():
    assert policy_version("a\r\nb\r\n") == policy_version("a\nb\n")


def test_policy_version_changes_with_policy_text():
    assert policy_version("Refunds within 30 days.") != policy_version("Refunds within 31 days.")


def test_tool_schema_version_ignores_tool_order():
    cancel = {"name": "cancel_pending_order", "parameters": {"order_id": "string"}}
    lookup = {"name": "get_order_details", "parameters": {"order_id": "string"}}
    assert tool_schema_version([cancel, lookup]) == tool_schema_version([lookup, cancel])
    assert tool_schema_version([cancel, lookup]).startswith("tools-")
    assert tool_schema_version([cancel]) != tool_schema_version([cancel, lookup])


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_canonical_json_rejects_non_finite_numbers(value):
    with pytest.raises(ValueError):
        argument_hash({"amount": {"nested": [value]}})
    with pytest.raises(ValueError):
        tool_schema_version([{"name": "t", "default": value}])


@pytest.mark.parametrize("value", [{1: "x"}, {"nested": {1: "x"}}, {"a": [{None: "x"}]}])
def test_hashes_reject_non_string_keys(value):
    with pytest.raises(TypeError, match="keys must be strings"):
        argument_hash(value)
    with pytest.raises(TypeError, match="keys must be strings"):
        tool_schema_version([value])


def test_hashes_reject_mixed_keys_that_would_collide():
    with pytest.raises(TypeError, match="keys must be strings"):
        argument_hash({1: "a", "1": "b"})


def test_stored_event_content_can_be_hashed():
    content = {"order_id": "#W2378156", "items": [{"id": "1"}]}
    event = make_event(EventType.TOOL_CALL, inputs=content)
    assert argument_hash(event.inputs) == argument_hash(content)
