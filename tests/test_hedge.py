"""Hedge commands when the model gives no usable reply.

The client already retried; the next market would most likely fail the same
way (and be billed), so the scan stops with one clear error instead of one
per market, and `analyze` doesn't report "no implication" for a failed call.
"""

import asyncio
from argparse import Namespace

import pytest

import hedge
from lib.gamma_client import Market
from lib.llm_client import LLMEmptyReply


def _market(i: int) -> Market:
    return Market(
        id=str(i), question=f"Q{i}?", slug=f"q{i}", condition_id=f"0x{i}",
        yes_token_id=f"y{i}", no_token_id=f"n{i}", yes_price=0.5, no_price=0.5,
        volume=0, volume_24h=0, liquidity=0, end_date="", active=True,
        closed=False, resolved=False, outcome=None,
    )


class _Gamma:
    async def get_trending_markets(self, limit):
        return [_market(i) for i in range(3)]

    async def get_market(self, market_id):
        return _market(int(market_id))


class _EmptyLLM:
    calls = 0

    def __init__(self, model):
        pass

    async def complete(self, messages, temperature=0.1):
        type(self).calls += 1
        raise LLMEmptyReply("LLM m returned no text (finish_reason=stop)")

    async def close(self):
        pass


@pytest.fixture
def empty_llm(monkeypatch):
    _EmptyLLM.calls = 0
    monkeypatch.setattr(hedge, "GammaClient", _Gamma)
    monkeypatch.setattr(hedge, "LLMClient", _EmptyLLM)
    return _EmptyLLM


def test_scan_stops_at_the_first_empty_reply(empty_llm, capsys):
    args = Namespace(query=None, limit=3, model="m", json=False,
                     min_coverage=None, tier=None)
    assert asyncio.run(hedge.cmd_scan(args)) == 1
    assert empty_llm.calls == 1
    err = capsys.readouterr().err
    assert err.count("returned no text") == 1
    assert err.count("--model") == 1


def test_analyze_reports_the_failure_not_no_implication(empty_llm, capsys):
    args = Namespace(market_id_1="1", market_id_2="2", model="m", json=False,
                     min_coverage=None)
    assert asyncio.run(hedge.cmd_analyze(args)) == 1
    out = capsys.readouterr()
    assert "No logical implication" not in out.out
    assert "returned no text" in out.err
