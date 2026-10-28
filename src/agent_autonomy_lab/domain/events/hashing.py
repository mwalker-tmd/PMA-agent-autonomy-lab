"""Content hashes that identify tool arguments, policy text, and tool schemas."""

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any


def canonical_json(value: Any) -> str:
    """Serialize ``value`` so equal data always gives the same string.

    Keys are sorted and whitespace is removed. Non-JSON values raise
    ``TypeError`` rather than being stringified, so a hash never depends on a
    ``repr``.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def argument_hash(arguments: Mapping[str, Any]) -> str:
    """Return ``sha256:<hex>`` of a tool call's arguments in canonical JSON.

    Lets an audit compare attempted actions without storing the arguments in
    the event. The hash is unsalted so that the same call hashes the same way
    across runs; low-entropy arguments such as an order ID can be recovered by
    guessing, so it is not a substitute for redaction.
    """
    return "sha256:" + _sha256(canonical_json(dict(arguments)))


def policy_version(policy_text: str) -> str:
    """Return ``policy-<12 hex>`` derived from the policy text.

    τ-bench stores the full policy (``info.environment_info.policy``) but no
    version, so the version is the start of the text's SHA-256. Line endings
    are normalized first so a checkout's newline style does not change it.
    """
    normalized = policy_text.replace("\r\n", "\n").replace("\r", "\n")
    return "policy-" + _sha256(normalized)[:12]


def tool_schema_version(tool_schemas: Sequence[Mapping[str, Any]]) -> str:
    """Return ``tools-<12 hex>`` derived from the tool schemas in canonical JSON.

    Schemas are sorted by their canonical form, so the order in which tools are
    listed does not change the version.
    """
    serialized = sorted(canonical_json(dict(schema)) for schema in tool_schemas)
    return "tools-" + _sha256("[" + ",".join(serialized) + "]")[:12]
