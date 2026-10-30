"""Remove secrets from everything the lab sends to LangSmith.

A ``Redactor`` replaces a secret with ``REDACTED`` wherever it finds one in
JSON data, by three rules:

- **Known values.** The value of every environment variable whose name marks
  it as a secret (``OPENAI_API_KEY``, ``LANGSMITH_API_KEY``, ``*_TOKEN``,
  ``*_SECRET``, ...) is replaced wherever it appears, whatever its format,
  in mapping keys as well as values, and also JSON-escaped once, as it
  appears inside JSON-encoded text such as tool-call ``arguments``. If two
  keys of one mapping redact to the same text, the last one's value is kept.
  Importing τ-bench loads the working directory's ``.env``, so these are the
  keys a lab process actually holds.
- **Known formats.** The LangSmith SDK's ``DEFAULT_SECRET_RULES``: provider key
  prefixes (``sk-``, ``lsv2_``, ...), ``Authorization``/``Bearer`` headers,
  ``NAME=value`` assignments with a secret name, and credentials in URLs.
- **Secret names.** The value under a mapping key such as ``api_key``,
  ``authorization``, ``x-api-key``, ``cookie``, ``access_token``,
  ``accessToken``, or ``password`` is replaced whole. ``prompt_tokens`` and
  ``maxTokens`` are not secret names.

The trace sink redacts every argument of every run and feedback item it
sends. The SDK adds metadata of its own (some ``LANGSMITH_*`` variables and a
git revision) after the sink hands over a run, so ``langsmith_client()``
also installs the redactor as the client's ``anonymizer``, which the SDK
applies to that metadata. The SDK's ``extra.runtime`` is not passed through
it: it is a fixed set of SDK, Python, and platform versions and CI commit
SHAs, with no credentials.
"""

import json
import os
import re
from collections.abc import Iterable, Mapping
from typing import Any

from langsmith import Client
from langsmith.anonymizer import DEFAULT_SECRET_RULES, SECRET_PLACEHOLDER

REDACTED = SECRET_PLACEHOLDER

# A secret's name has one of these words as a component, separated by ``_``
# or ``-``, by a camelCase boundary, or at either end.
_SECRET_WORDS = (
    r"api[_-]?key|apikey|access[_-]?key|private[_-]?key|authorization|cookie"
    r"|token|secret|password|passwd|credentials?"
)
SECRET_NAME = re.compile(rf"(?:^|[_-])(?:{_SECRET_WORDS})(?:[_-]|$)", re.IGNORECASE)
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

# Shorter values (``true``, ``1``) are settings, not credentials.
MIN_SECRET_LENGTH = 8


def is_secret_name(name: str) -> bool:
    """Whether a variable or field called ``name`` holds a credential."""
    return bool(SECRET_NAME.search(_CAMEL_BOUNDARY.sub("_", name)))


class Redactor:
    """Replaces secrets in JSON data; see the module docstring for the rules.

    ``secrets`` are exact values to remove; values shorter than
    ``MIN_SECRET_LENGTH`` are ignored. Calling a redactor returns a redacted
    copy and leaves its argument unchanged.
    """

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        values = set()
        for secret in secrets:
            if isinstance(secret, str) and len(secret) >= MIN_SECRET_LENGTH:
                values.update(_escaped_forms(secret))
        # Longest first, so a secret that contains another is removed whole.
        self._secrets = sorted(values, key=len, reverse=True)

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "Redactor":
        """A redactor for the secrets in ``environ`` (default: this process's environment)."""
        environ = os.environ if environ is None else environ
        return cls(value for name, value in environ.items() if is_secret_name(name))

    def __call__(self, data: Any) -> Any:
        if isinstance(data, Mapping):
            return dict(self._entry(key, value) for key, value in data.items())
        if isinstance(data, list | tuple):
            return [self(item) for item in data]
        if isinstance(data, str):
            return self.text(data)
        return data

    def _entry(self, key: Any, value: Any) -> tuple[Any, Any]:
        """One mapping entry, redacted: the key is checked by its original name."""
        if not isinstance(key, str):
            return key, self(value)
        if is_secret_name(key) and value not in (None, ""):
            return self.text(key), REDACTED
        return self.text(key), self(value)

    def text(self, value: str) -> str:
        for secret in self._secrets:
            value = value.replace(secret, REDACTED)
        for rule in DEFAULT_SECRET_RULES:
            value = rule["pattern"].sub(rule["replace"] or REDACTED, value)
        return value


def _escaped_forms(secret: str) -> set[str]:
    """``secret`` as written, and as it appears inside JSON-encoded text."""
    return {
        secret,
        json.dumps(secret)[1:-1],
        json.dumps(secret, ensure_ascii=False)[1:-1],
    }


def langsmith_client(redactor: Redactor | None = None, **kwargs: Any) -> Client:
    """A ``langsmith.Client`` that also redacts the metadata the SDK adds to each run.

    ``kwargs`` go to ``Client``; ``redactor`` defaults to
    ``Redactor.from_environment()``.
    """
    return Client(anonymizer=redactor or Redactor.from_environment(), **kwargs)
