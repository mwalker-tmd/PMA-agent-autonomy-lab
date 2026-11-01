"""The writes a task's reference expects, and how a run's writes are compared with them.

Answer-key data, so it is for offline evaluation only (docs/EVALUATION.md, "Hidden
ground truth"). Writes are compared as multisets (AA-5): independent writes may come
in any order, and list arguments are multisets of item IDs or of old and new item
pairs. Reference actions that fail on gold replay are dropped (AA-4).
"""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agent_autonomy_lab.domain.events import canonical_json, json_data

DEFECT_TASKS = frozenset({"20", "21", "36", "37", "64", "100", "105"})
"""Retail tasks whose reference is faulty at τ-bench ``0c69df4`` (docs/DECISIONS.md, ADR-019)."""

MULTI_ITEM_DEFECT_TOOL = "modify_pending_order_items"
_PAIRED_TOOLS = frozenset({MULTI_ITEM_DEFECT_TOOL, "exchange_delivered_order_items"})
_ITEM_LIST_TOOLS = frozenset({"return_delivered_order_items"})


def _all_text(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def normalized_write(name: str, arguments: Mapping[str, Any]) -> str:
    """One write's comparable form, in which list order does not count.

    Arguments that do not have the expected shape stay as they are, so they can
    only equal the same malformed arguments.
    """
    normal = dict(arguments)
    old, new = normal.get("item_ids"), normal.get("new_item_ids")
    if name in _PAIRED_TOOLS and _all_text(old) and _all_text(new) and len(old) == len(new):
        del normal["item_ids"], normal["new_item_ids"]
        normal["item_pairs"] = sorted(zip(old, new, strict=True))
    elif name in _ITEM_LIST_TOOLS and _all_text(old):
        normal["item_ids"] = sorted(old)
    return canonical_json({"name": name, "arguments": normal})


def is_multi_item_modification(name: str, arguments: Mapping[str, Any]) -> bool:
    """Whether upstream's stale-variant defect makes this write depend on item order."""
    items = arguments.get("item_ids")
    return name == MULTI_ITEM_DEFECT_TOOL and isinstance(items, list) and len(items) > 1


@dataclass(frozen=True)
class WriteCall:
    """A reference write: a tool and its arguments."""

    name: str
    arguments: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "arguments", json_data(self.arguments))

    @property
    def normalized(self) -> str:
        return normalized_write(self.name, self.arguments)


@dataclass(frozen=True)
class ReferenceWrites:
    """One task's reference writes: those a run is expected to make, and those dropped."""

    task_id: str
    expected: tuple[WriteCall, ...] = ()
    dropped: tuple[WriteCall, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "expected", tuple(self.expected))
        object.__setattr__(self, "dropped", tuple(self.dropped))

    @property
    def multiset(self) -> Counter[str]:
        return Counter(write.normalized for write in self.expected)
