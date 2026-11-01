"""The ``ReferenceWriteSource`` port and its in-memory test double."""

from collections.abc import Mapping
from typing import Protocol

from agent_autonomy_lab.domain.reference_writes import ReferenceWrites


class ReferenceWriteSource(Protocol):
    """Gives a benchmark task's reference writes. Answer-key data: offline only."""

    def reference_writes(self, task_id: str) -> ReferenceWrites | None:
        """The task's reference writes, or ``None`` if the benchmark has no such task."""
        ...


class CannedReferenceWrites:
    """Returns prepared reference writes by task ID, and ``None`` for any other task."""

    def __init__(self, references: Mapping[str, ReferenceWrites] | None = None) -> None:
        self.references = dict(references or {})

    def reference_writes(self, task_id: str) -> ReferenceWrites | None:
        return self.references.get(task_id)
