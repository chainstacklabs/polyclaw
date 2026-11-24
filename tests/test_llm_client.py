"""Empty model replies: retried, then a clear error (not a NoneType crash).

Live 2026-11-23 (hedge scan, deepseek/deepseek-v4.1-flash): a reply with
`content: null` crashed implication extraction with
"'NoneType' object has no attribute 'strip'".
"""

import asyncio

import httpx
import pytest

from lib import llm_client
from lib.llm_client import LLMClient, LLMEmptyReply


def _llm(replies: list[dict]) -> LLMClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=replies.pop(0))

    llm = LLMClient(model="m", api_key="k")
    llm._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return llm


def _reply(content, finish="stop") -> dict:
    return {"choices": [{"message": {"content": content}, "finish_reason": finish}]}


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def instant(_seconds):
        return None

    monkeypatch.setattr(llm_client.asyncio, "sleep", instant)


def test_empty_reply_is_retried():
    llm = _llm([_reply(None), _reply("ok")])
    assert asyncio.run(llm.complete([{"role": "user", "content": "hi"}])) == "ok"


def test_always_empty_raises_a_clear_error():
    llm = _llm([_reply(None)] * llm_client.LLM_MAX_RETRIES)
    with pytest.raises(LLMEmptyReply, match="LLM m returned no text.*finish_reason=stop"):
        asyncio.run(llm.complete([{"role": "user", "content": "hi"}]))


def test_out_of_budget_is_not_retried():
    # Same prompt, same budget: a retry would be billed and empty again.
    replies = [_reply(None, "length"), _reply("ok")]
    llm = _llm(replies)
    with pytest.raises(LLMEmptyReply, match="finish_reason=length"):
        asyncio.run(llm.complete([{"role": "user", "content": "hi"}]))
    assert len(replies) == 1  # one call made


def test_error_body_without_choices_is_retried():
    # OpenRouter can answer HTTP 200 with only an error object.
    llm = _llm([{"error": {"message": "provider down"}}, _reply("ok")])
    assert asyncio.run(llm.complete([{"role": "user", "content": "hi"}])) == "ok"


def test_error_body_reason_is_reported():
    llm = _llm([{"error": {"message": "provider down"}}] * llm_client.LLM_MAX_RETRIES)
    with pytest.raises(LLMEmptyReply, match="provider down"):
        asyncio.run(llm.complete([{"role": "user", "content": "hi"}]))


def test_whitespace_reply_is_empty():
    llm = _llm([_reply("  \n"), _reply("ok")])
    assert asyncio.run(llm.complete([{"role": "user", "content": "hi"}])) == "ok"


def test_reply_cut_by_a_provider_error_is_retried():
    # A stream that fails midway ends with finish_reason "error": the text is
    # a fragment and parses as a wrong (or no) answer.
    llm = _llm([_reply('{"implications": [', "error"), _reply("ok")])
    assert asyncio.run(llm.complete([{"role": "user", "content": "hi"}])) == "ok"
