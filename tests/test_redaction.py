"""Secret redaction rules, checked on data alone; the sent payloads are in test_trace_leaks."""

import pytest

from agent_autonomy_lab.adapters.langsmith import Redactor
from agent_autonomy_lab.adapters.langsmith.redaction import REDACTED, is_secret_name

# Fake credentials in real providers' formats. None is a real key.
FAKE_OPENAI_KEY = "sk-proj-PLANTEDFAKEKEY0000000000000000000000"
FAKE_LANGSMITH_KEY = "lsv2_pt_PLANTEDFAKEKEY000000000000000000000000_0000000000"
# A credential in no known format, found only because the environment holds it.
FAKE_PLAIN_TOKEN = "planted-plain-credential-0001"


@pytest.mark.parametrize(
    "name",
    [
        "api_key", "OPENAI_API_KEY", "LANGSMITH_API_KEY", "apiKey", "x-api-key", "Authorization",
        "Proxy-Authorization", "cookie", "Set-Cookie", "access_token", "GITHUB_TOKEN", "secret",
        "client_secret", "AWS_SECRET_ACCESS_KEY", "password", "credentials",
    ],
)  # fmt: skip
def test_secret_names(name):
    assert is_secret_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "prompt_tokens", "completion_tokens", "max_tokens", "total_tokens", "tokenizer",
        "order_id", "payment_method_id", "reasoning_effort", "LANGSMITH_ENDPOINT", "keyboard",
    ],
)  # fmt: skip
def test_names_that_are_not_secret(name):
    assert not is_secret_name(name)


def test_values_under_secret_names_are_replaced_whole():
    redact = Redactor()
    data = {"model_args": {"api_key": "anything-at-all", "reasoning_effort": "medium", "seed": 1}}
    assert redact(data) == {
        "model_args": {"api_key": REDACTED, "reasoning_effort": "medium", "seed": 1}
    }


def test_known_formats_are_replaced_inside_text():
    redact = Redactor()
    text = f"call failed with key {FAKE_OPENAI_KEY}; header Authorization: Bearer abcdef0123456789"
    redacted = redact(text)
    assert FAKE_OPENAI_KEY not in redacted and "abcdef0123456789" not in redacted
    assert redacted.startswith("call failed with key ")


def test_environment_secrets_are_replaced_in_any_format():
    environ = {
        "TAU2_SERVICE_TOKEN": FAKE_PLAIN_TOKEN,
        "LANGSMITH_TRACING": "true",
        "LANGSMITH_PROJECT": "agent-autonomy-lab-synthetic",
    }
    redact = Redactor.from_environment(environ)
    assert redact([f"x{FAKE_PLAIN_TOKEN}y"]) == [f"x{REDACTED}y"]
    assert redact("agent-autonomy-lab-synthetic") == "agent-autonomy-lab-synthetic"


def test_short_values_are_not_treated_as_secrets():
    redact = Redactor.from_environment({"SOME_TOKEN": "true"})
    assert redact("true story") == "true story"


def test_a_secret_containing_another_is_removed_whole():
    redact = Redactor(["abcdefgh", "abcdefgh-ijklmnop"])
    assert redact("abcdefgh-ijklmnop") == REDACTED


def test_redaction_leaves_its_argument_unchanged():
    data = {"headers": {"Authorization": "Bearer abcdef0123456789"}, "note": FAKE_LANGSMITH_KEY}
    original = {"headers": dict(data["headers"]), "note": data["note"]}
    Redactor()(data)
    assert data == original


def test_numbers_booleans_and_nulls_pass_through():
    redact = Redactor()
    assert redact({"n": 1, "f": 0.5, "b": True, "none": None, "api_key": None}) == {
        "n": 1, "f": 0.5, "b": True, "none": None, "api_key": None,
    }  # fmt: skip
