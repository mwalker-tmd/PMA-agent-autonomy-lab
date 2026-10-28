import hashlib

import pytest

from agent_autonomy_lab.domain.events import (
    argument_hash,
    canonical_json,
    policy_version,
    tool_schema_version,
)

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
