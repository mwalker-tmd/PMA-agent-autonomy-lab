"""The scorecard's figures, counted from the saved scores, and the re-score's comparison.

Every table here is built from rows of ``simulations.jsonl`` and the re-scored rubrics, so
no figure is typed by hand. Each metric keeps its own numerator, denominator, and non-scored
counts, and nothing combines them into one score (docs/EVALUATION.md, "Rules for every metric").
"""

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from agent_autonomy_lab.application.adjudication import (
    CONFIRMATION_KEY,
    JUDGE_KEYS,
    RUBRIC_LABELS,
    judge_units,
)

# A new ID is drawn for each raw output, so a replay shows only whether a result has one. The
# other two fields are on the re-score's records, and the batch report leaves them out.
_NOT_COMPARED = ("raw_output_id", "scenario_id", "simulation_id")
_DECIDED = ("PASS", "FAIL")

_OUTCOME_METRICS = (("Task success (τ-bench reward)", "task_success"),)
_ROLL_UPS = (
    ("Policy compliance, part A (per simulation)", "policy_compliance"),
    ("Authentication compliance (per simulation)", "authentication_compliance"),
    ("Confirmation compliance (per simulation)", "confirmation_compliance"),
)
_CONDUCT_METRICS = (
    ("Tool correctness: expected writes", "expected_writes"),
    ("Unauthorized attempts, no gate active (passes = not unauthorized)", "unauthorized_attempts"),
    (
        "Unauthorized executions, no gate active (passes = not unauthorized)",
        "unauthorized_executions",
    ),
)
_CHECKS = (
    "authentication_order",
    "user_access",
    "message_form",
    "order_status",
    "status_read",
    "item_change_once",
    "item_variant",
    "cancellation_reason",
    "refund_method",
    "gift_card_balance",
    "method_owner",
    "payment_change",
    "overwritten_write",
    "transfer_message",
    "autonomy_level",
)
_JUDGE_NAMES = {
    "clarity_customer_effort": "Clarity",
    "conversation_efficiency": "Efficiency",
    "escalation_correctness": "Escalation",
    CONFIRMATION_KEY: "Confirmation (per write)",
}


def comparable(result: Mapping[str, Any]) -> dict[str, Any]:
    """A saved result as a replay must reproduce it, whether or not it has a raw output."""
    kept = {key: value for key, value in result.items() if key not in _NOT_COMPARED}
    return {**kept, "has_raw_output": result.get("raw_output_id") is not None}


def compare_results(
    recorded: Sequence[Mapping[str, Any]], replayed: Sequence[Mapping[str, Any]], where: str
) -> dict[str, Any]:
    """Count equal results by position, deterministic and judged apart.

    A count of results differing is also a difference, so a replay that loses a row fails.
    """
    report: dict[str, Any] = {
        "deterministic": {"compared": 0, "identical": 0},
        "judged": {"compared": 0, "identical": 0},
        "differences": [],
    }
    if len(recorded) != len(replayed):
        report["differences"].append(f"{where}: {len(recorded)} recorded, {len(replayed)} replayed")
    for before, after in zip(recorded, replayed, strict=False):
        part = report["judged" if before["evaluation_key"] in JUDGE_KEYS else "deterministic"]
        part["compared"] += 1
        if comparable(before) == comparable(after):
            part["identical"] += 1
        else:
            report["differences"].append(f"{where}: {before['evaluation_key']}")
    return report


def merge_comparisons(reports: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {
        "deterministic": {"compared": 0, "identical": 0},
        "judged": {"compared": 0, "identical": 0},
        "differences": [],
    }
    for report in reports:
        for part in ("deterministic", "judged"):
            for name in ("compared", "identical"):
                merged[part][name] += report[part][name]
        merged["differences"].extend(report["differences"])
    return merged


def evidence_coverage(
    results: Iterable[Mapping[str, Any]], known_ids: Iterable[str]
) -> dict[str, int]:
    """How many results cite evidence, and how many of the cited IDs exist in the trace."""
    known = set(known_ids)
    counts = Counter()
    for result in results:
        cited = result.get("evidence_ids") or []
        counts["decided"] += result["status"] in _DECIDED
        counts["decided_citing_evidence"] += result["status"] in _DECIDED and bool(cited)
        counts["ids_cited"] += len(cited)
        counts["ids_found"] += sum(event_id in known for event_id in cited)
    return {
        name: counts[name]
        for name in ("decided", "decided_citing_evidence", "ids_cited", "ids_found")
    }


def status_counts(rows: Iterable[Mapping[str, Any]], key: str) -> dict[str, int]:
    statuses = Counter(
        result["status"]
        for row in rows
        for result in row["results"]
        if result["evaluation_key"] == key
    )
    return {
        "numerator": statuses["PASS"],
        "denominator": statuses["PASS"] + statuses["FAIL"],
        "not_applicable": statuses["NOT_APPLICABLE"],
        "insufficient_evidence": statuses["INSUFFICIENT_EVIDENCE"],
        "evaluator_error": statuses["EVALUATOR_ERROR"],
        "invalid": statuses["INVALID"],
    }


def rollup_counts(rows: Iterable[Mapping[str, Any]], name: str) -> dict[str, int]:
    statuses = Counter(row["roll_ups"].get(name) for row in rows)
    return {
        "numerator": statuses["PASS"],
        "denominator": statuses["PASS"] + statuses["FAIL"],
        "not_applicable": statuses["NOT_APPLICABLE"],
        "insufficient_evidence": statuses["INSUFFICIENT_EVIDENCE"],
        "evaluator_error": statuses["EVALUATOR_ERROR"],
        "invalid": statuses["INVALID"],
    }


def component_counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    """Each reward component's counts, with a component the task does not check as not applicable.

    τ-bench gives ``NL_ASSERTION: 1.0`` to a task with no assertions, which checked nothing, so
    only a run that shows the check was made enters the denominator (docs/EVALUATION.md).
    """
    components: dict[str, dict[str, int]] = {}
    for row in rows:
        benchmark = row["benchmark"]
        checked = {
            "DB": benchmark.get("db_match") is not None,
            "NL_ASSERTION": bool(benchmark.get("nl_assertions")),
        }
        for name, score in (benchmark.get("reward_breakdown") or {}).items():
            part = components.setdefault(
                name,
                {
                    "numerator": 0,
                    "denominator": 0,
                    "not_applicable": 0,
                    "insufficient_evidence": 0,
                    "evaluator_error": 0,
                    "invalid": 0,
                },
            )
            if row["run_class"] == "invalid":
                part["invalid"] += 1
            elif not checked.get(name, True):
                part["not_applicable"] += 1
            else:
                part["denominator"] += 1
                part["numerator"] += score == 1.0
    return components


def _table(header: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(lines)


_COUNT_HEADER = ("Numerator / denominator", "Not applicable", "Insufficient", "Error", "Invalid")


def _count_cells(counts: Mapping[str, int]) -> tuple:
    return (
        f"{counts['numerator']} / {counts['denominator']}",
        counts["not_applicable"],
        counts["insufficient_evidence"],
        counts["evaluator_error"],
        counts["invalid"],
    )


def metrics_table(rows: Sequence[Mapping[str, Any]]) -> str:
    """The outcome and conduct metrics, one row each, with no combined figure."""
    table: list[tuple] = []
    for label, key in _OUTCOME_METRICS:
        table.append(("Outcome", label, *_count_cells(status_counts(rows, key))))
    for name, part in component_counts(rows).items():
        table.append(("Outcome", f"Reward component {name}", *_count_cells(part)))
    for label, name in _ROLL_UPS:
        table.append(("Conduct", label, *_count_cells(rollup_counts(rows, name))))
    for label, key in _CONDUCT_METRICS:
        table.append(("Conduct", label, *_count_cells(status_counts(rows, key))))
    return _table(("Kind", "Metric", *_COUNT_HEADER), table)


def checks_table(rows: Sequence[Mapping[str, Any]]) -> str:
    """The deterministic checks under the roll-ups, each counted by its own unit."""
    return _table(
        ("Check", *_COUNT_HEADER),
        ((f"`{key}`", *_count_cells(status_counts(rows, key))) for key in _CHECKS),
    )


def judges_table(
    rows: Sequence[Mapping[str, Any]], rescored_rows: Sequence[Mapping[str, Any]]
) -> str:
    """Each judge's v1 from the run, beside its v2 re-scored from the same saved runs."""
    table = []
    for key in RUBRIC_LABELS:
        table.append(
            (_JUDGE_NAMES[key], "v1 (as run)", *_count_cells(status_counts(rows, key))),
        )
        table.append(("", "v2 (re-scored)", *_count_cells(status_counts(rescored_rows, key))))
    table.append(
        (
            _JUDGE_NAMES[CONFIRMATION_KEY],
            "v1, unchanged",
            *_count_cells(status_counts(rows, CONFIRMATION_KEY)),
        )
    )
    return _table(("Judge", "Version", *_COUNT_HEADER), table)


def outcome_conduct_table(rows: Sequence[Mapping[str, Any]]) -> str:
    """Reward against part A: the off-diagonal cells are why neither stands in for the other."""
    columns = ("PASS", "FAIL", "INSUFFICIENT_EVIDENCE")
    counts = Counter(
        (row["benchmark"]["reward"] == 1.0, row["roll_ups"]["policy_compliance"]) for row in rows
    )
    known = sum(counts[(rewarded, column)] for rewarded in (True, False) for column in columns)
    if known != len(rows):
        raise ValueError("a simulation has a part A status the table does not show")
    return _table(
        ("Simulations", "Part A `PASS`", "Part A `FAIL`", "Part A undecided"),
        (
            (name, *(counts[(rewarded, column)] for column in columns))
            for name, rewarded in (("Reward 1", True), ("Reward 0", False))
        ),
    )


def _row_for(rows: Sequence[Mapping[str, Any]], task: str, trial: int) -> Mapping[str, Any]:
    return next(row for row in rows if row["task_id"] == task and row["trial"] == trial)


def contrast_table(rows: Sequence[Mapping[str, Any]], contrasts: Sequence[tuple[str, int]]) -> str:
    table = []
    for task, trial in contrasts:
        row = _row_for(rows, task, trial)
        rolls = row["roll_ups"]
        table.append(
            (
                f"`{task}`, {trial}",
                row["benchmark"]["reward"],
                row["benchmark"]["db_match"],
                rolls["policy_compliance"],
                rolls["confirmation_compliance"],
                rolls["authentication_compliance"],
                f"`{row['thread_id']}`",
                f"`{row['root_run_id']}`",
            )
        )
    return _table(
        (
            "Task, trial",
            "Reward",
            "End state matched",
            "Part A",
            "Confirmation",
            "Authentication",
            "Thread",
            "Root run",
        ),
        table,
    )


def operational_table(manifest: Mapping[str, Any]) -> str:
    operational, cost = manifest["operational"], manifest["cost"]
    latency = operational["latency_seconds"]
    tools, retries = operational["failed_tools"], operational["tool_retries"]
    turns, tokens = operational["turns"], operational["tokens"]
    return _table(
        ("Measure", "Value"),
        (
            (
                "Cost at list price, cap $" + f"{cost['cap_usd']:g}",
                f"${cost['list_price_usd']:.4f}",
            ),
            ("Simulation latency, median / p95", _seconds(latency["simulation"])),
            ("Agent turn latency, median / p95", _seconds(latency["agent_turn"])),
            ("Failed tool results", f"{tools['numerator']} / {tools['denominator']}"),
            ("Calls repeating a failed call", f"{retries['numerator']} / {retries['denominator']}"),
            ("Messages / simulations", f"{turns['numerator']} / {turns['denominator']}"),
            ("Prompt / completion tokens", f"{tokens['prompt']} / {tokens['completion']}"),
        ),
    )


def _seconds(spread: Mapping[str, Any]) -> str:
    return f"{spread['median']:.1f}s / {spread['p95']:.1f}s (n = {spread['n']})"


def reproduction_table(check: Mapping[str, Any]) -> str:
    """What the last re-score reproduced, from the file it wrote."""
    baseline, rubric = check["baseline"], check["rubric_v2"]
    repeats = check["repeats"]

    def line(part: Mapping[str, int]) -> str:
        return f"{part['identical']} / {part['compared']}"

    return _table(
        ("Results", "Identical / compared", "Source"),
        (
            ("Baseline, deterministic", line(baseline["deterministic"]), "Saved runs"),
            ("Baseline, judges", line(baseline["judged"]), "`judges.cassette.json` of each run"),
            ("Baseline, roll-ups per simulation", line(baseline["roll_ups"]), "The results above"),
            ("Rubric v2, all simulations", line(rubric["judged"]), "`rescore-rubric-v2`"),
            ("Repeats, v1", line(repeats["v1"]), "`run-0` to `run-2`"),
            ("Repeats, v2", line(repeats["v2"]), "`rubric-v2/run-0` to `run-2`"),
        ),
    )


def evidence_table(check: Mapping[str, Any]) -> str:
    """How many decided results cite evidence, and how many cited IDs were found."""
    baseline = check["baseline"]["evidence"]
    rubric = check["rubric_v2"]["evidence_in_rebuilt_trace"]

    def cells(part: Mapping[str, int]) -> tuple[str, str]:
        return (
            f"{part['decided_citing_evidence']} / {part['decided']}",
            f"{part['ids_found']} / {part['ids_cited']}",
        )

    return _table(
        ("Results", "`PASS` or `FAIL` citing evidence", "Cited IDs found", "Searched in"),
        (
            ("Baseline, as run", *cells(baseline), "Saved `trace_events.jsonl`, as sent"),
            ("Rubric v2", *cells(rubric), "The trace rebuilt from the saved run"),
        ),
    )


def _judge_cell(result: Mapping[str, Any] | None) -> str:
    if result is None:
        return "not run"
    score = result.get("score")
    shown = result["status"] if score is None else f"{result['status']} ({score:g})"
    return f"{shown}: {_one_line(result.get('reason'))}"


def _one_line(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").replace("|", "\\|").strip()


def sample_judgments(
    cases: Sequence[Mapping[str, Any]],
    labels: Sequence[Mapping[str, Any]],
    rows: Mapping[str, Mapping[str, Any]],
    rescored: Mapping[str, Mapping[str, Any]],
) -> str:
    """Every adjudicated case: the maintainer's label beside each judge's verdict and reason."""
    by_case = {label["case_id"]: label for label in labels}
    parts = []
    for case in cases:
        label = by_case[case["case_id"]]
        row, after = rows[case["simulation_id"]], rescored[case["simulation_id"]]
        before_units, after_units = judge_units(row["results"]), judge_units(after["results"])
        table = []
        for (key, unit), result in before_units.items():
            if key == CONFIRMATION_KEY:
                human = label["confirmation_by_write"][unit]
                name = f"Confirmation, write {unit + 1}"
                table.append((name, human, _judge_cell(result), "unchanged"))
            else:
                human = label[RUBRIC_LABELS[key]]
                table.append(
                    (
                        _JUDGE_NAMES[key],
                        human,
                        _judge_cell(result),
                        _judge_cell(after_units.get((key, unit))),
                    )
                )
        # The maintainer's words as recorded, quoted so nothing is added to them.
        note = f'"{_one_line(label["note"])}"' if label.get("note") else "none."
        parts.append(
            "\n".join(
                (
                    f"## {case['case_id']}: task `{case['task_id']}`, trial {case['trial']}",
                    "",
                    f"Reward {row['benchmark']['reward']:g}, part A "
                    f"`{row['roll_ups']['policy_compliance']}`. Handled correctly, per the "
                    f"maintainer: {label['handled_correctly']}. Note: {note} "
                    f"Thread `{row['thread_id']}`.",
                    "",
                    _table(("Judge", "Maintainer", "v1", "v2"), table),
                )
            )
        )
    return "\n\n".join(parts)


def replace_blocks(text: str, blocks: Mapping[str, str]) -> str:
    """Put each generated block between its markers, and refuse a marker that is missing."""
    for name, body in blocks.items():
        start, end = f"<!-- generated:{name} -->", f"<!-- /generated:{name} -->"
        pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
        if len(pattern.findall(text)) != 1:
            raise ValueError(f"expected one generated block named {name}")
        found = pattern.search(text)
        text = f"{text[: found.start()]}{start}\n{body}\n{end}{text[found.end() :]}"
    return text
