"""Read a retail task's reference writes and keep those that succeed on gold replay.

τ-bench replays a task's reference actions on a fresh environment to get the target
end state, and logs and skips any that raise (``EnvironmentEvaluator``). This does the
same, through the same ``make_tool_call``, so the writes it expects are the ones that
shaped that end state. No model is called, and the environment is never registered.
"""

from pathlib import Path

from tau2.domains.retail.data_model import RetailDB
from tau2.domains.retail.environment import get_environment, get_tasks
from tau2.domains.retail.utils import RETAIL_DB_PATH

from agent_autonomy_lab.domain.reference_writes import ReferenceWrites, WriteCall


class GoldReplayReferenceWrites:
    """``ReferenceWriteSource`` over τ-bench's retail tasks and ``db.json``."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db = RetailDB.load(str(db_path or RETAIL_DB_PATH))
        self._tasks = {task.id: task for task in get_tasks(task_split_name=None)}
        self._replayed: dict[str, ReferenceWrites] = {}

    def reference_writes(self, task_id: str) -> ReferenceWrites | None:
        task = self._tasks.get(task_id)
        if task is None:
            return None
        if task_id not in self._replayed:
            self._replayed[task_id] = self._replay(task)
        return self._replayed[task_id]

    def _replay(self, task) -> ReferenceWrites:
        environment = get_environment(db=self._db)  # takes its own deep copy
        expected: list[WriteCall] = []
        dropped: list[WriteCall] = []
        criteria = task.evaluation_criteria
        for action in (criteria.actions if criteria else None) or ():
            mutates = environment.tools.has_tool(
                action.name
            ) and environment.tools.tool_mutates_state(action.name)
            write = WriteCall(action.name, action.arguments)
            try:
                environment.make_tool_call(
                    tool_name=action.name, requestor=action.requestor, **action.arguments
                )
            except Exception:  # τ-bench skips any reference action that raises, whatever raised
                if mutates:
                    dropped.append(write)
                continue
            if mutates:
                expected.append(write)
        return ReferenceWrites(task.id, tuple(expected), tuple(dropped))
