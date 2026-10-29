"""Remove secrets from everything the lab sends to LangSmith.

A ``Redactor`` replaces a secret with ``REDACTED`` wherever it finds one in
JSON data, by three rules:

- **Known values.** The value of every environment variable whose name marks
  it as a secret (``OPENAI_API_KEY``, ``LANGSMITH_API_KEY``, ``*_TOKEN``,
  ``*_SECRET``, ...) is replaced wherever it appears, whatever its format.
  Importing τ-bench loads the working directory's ``.env``, so these are the
  keys a lab process actually holds.
- **Known formats.** The LangSmith SDK's ``DEFAULT_SECRET_RULES``: provider key
  prefixes (``sk-``, ``lsv2_``, ...), ``Authorization``/``Bearer`` headers,
  ``NAME=value`` assignments with a secret name, and credentials in URLs.
- **Secret names.** The value under a mapping key such as ``api_key``,
  ``authorization``, ``x-api-key``, ``cookie``, ``access_token``, or
  ``password`` is replaced whole. ``prompt_tokens`` and ``max_tokens`` are
  not secret names.

The trace sink redacts run inputs, outputs, metadata, errors, and events, and
feedback values and comments. The SDK adds metadata of its own (some
``LANGSMITH_*`` variables and a git revision) after the sink hands over a
run, so ``langsmith_client()`` also installs the redactor as the client's
``anonymizer``, which the SDK applies to that metadata.
"""

import os
import re
from collections.abc import Iterable, Mapping
from typing import Any

from langsmith import Client
from langsmith.anonymizer import DEFAULT_SECRET_RULES, SECRET_PLACEHOLDER

REDACTED = SECRET_PLACEHOLDER

# A secret's name has one of these words as a component, separated by ``_``
# or ``-`` or at either end.
_SECRET_WORDS = (
    r"api[_-]?key|apikey|access[_-]?key|private[_-]?key|authorization|cookie"
    r"|token|secret|password|passwd|credentials?"
)
SECRET_NAME = re.compile(rf"(?:^|[_-])(?:{_SECRET_WORDS})(?:[_-]|$)", re.IGNORECASE)

# Shorter values (``true``, ``1``) are settings, not credentials.
MIN_SECRET_LENGTH = 8


def is_secret_name(name: str) -> bool:
    """Whether a variable or field called ``name`` holds a credential."""
    return bool(SECRET_NAME.search(name))


class Redactor:
    """Replaces secrets in JSON data; see the module docstring for the rules.

    ``secrets`` are exact values to remove; values shorter than
    ``MIN_SECRET_LENGTH`` are ignored. Calling a redactor returns a redacted
    copy and leaves its argument unchanged.
    """

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        values = {s for s in secrets if isinstance(s, str) and len(s) >= MIN_SECRET_LENGTH}
        # Longest first, so a secret that contains another is removed whole.
        self._secrets = sorted(values, key=len, reverse=True)

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "Redactor":
        """A redactor for the secrets in ``environ`` (default: this process's environment)."""
        environ = os.environ if environ is None else environ
        return cls(value for name, value in environ.items() if is_secret_name(name))

    def __call__(self, data: Any) -> Any:
        if isinstance(data, Mapping):
            return {
                key: REDACTED
                if isinstance(key, str) and is_secret_name(key) and value not in (None, "")
                else self(value)
                for key, value in data.items()
            }
        if isinstance(data, list | tuple):
            return [self(item) for item in data]
        if isinstance(data, str):
            return self.text(data)
        return data

    def text(self, value: str) -> str:
        for secret in self._secrets:
            value = value.replace(secret, REDACTED)
        for rule in DEFAULT_SECRET_RULES:
            value = rule["pattern"].sub(rule["replace"] or REDACTED, value)
        return value


def langsmith_client(redactor: Redactor | None = None, **kwargs: Any) -> Client:
    """A ``langsmith.Client`` that also redacts the metadata the SDK adds to each run.

    ``kwargs`` go to ``Client``; ``redactor`` defaults to
    ``Redactor.from_environment()``.
    """
    return Client(anonymizer=redactor or Redactor.from_environment(), **kwargs)
