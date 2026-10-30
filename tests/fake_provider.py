"""A scripted OpenAI Responses API for running τ-bench offline."""

import json
from collections.abc import Iterable

import httpx


def text_reply(text: str) -> dict:
    return {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def tool_reply(call_id: str, name: str, arguments: dict) -> dict:
    return {
        "type": "function_call",
        "call_id": call_id,
        "name": name,
        "arguments": json.dumps(arguments),
    }


def task_0_outputs() -> list[dict]:
    """Retail task 0, shortened: the user asks, the agent looks them up and answers."""
    return [
        text_reply("Hi, I want to exchange two items. I'm Yusuf Rossi, zip 19122."),
        tool_reply(
            "call_1",
            "find_user_id_by_name_zip",
            {"first_name": "Yusuf", "last_name": "Rossi", "zip": "19122"},
        ),
        text_reply("I found your account. Which order?"),
        text_reply("###STOP###"),
    ]


class ScriptedProvider(httpx.BaseTransport):
    """Answers each request with the next scripted output item; records what it was sent."""

    def __init__(self, outputs: Iterable[dict]) -> None:
        self._outputs = iter(outputs)
        self.requests: list[dict] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        n = len(self.requests)
        item = {"id": f"item_{n}", "status": "completed", **next(self._outputs)}
        payload = {
            "id": f"resp_{n}",
            "object": "response",
            "created_at": 1761800000 + n,
            "status": "completed",
            "model": body["model"],
            "output": [item],
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        }
        return httpx.Response(200, json=payload, request=request)
