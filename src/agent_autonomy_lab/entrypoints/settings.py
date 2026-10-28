"""Runtime settings read from the environment by the composition root."""

import os
from collections.abc import Mapping

DEFAULT_LANGSMITH_PROJECT = "agent-autonomy-lab-synthetic"


def langsmith_project(env: Mapping[str, str] | None = None) -> str:
    """Return the LangSmith project name.

    Read from ``LANGSMITH_PROJECT`` so each cohort can use its own project;
    an unset or blank value falls back to the default synthetic project.
    """
    env = os.environ if env is None else env
    return env.get("LANGSMITH_PROJECT", "").strip() or DEFAULT_LANGSMITH_PROJECT
