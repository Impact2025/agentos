"""Regressietest voor de LLM-spend-circuit-breaker.

Voorkomt een herhaling van de 'quota in één dag leeg'-bug: als het dagbudget
op is, mogen autonome jobs (content-improver, biweekly, radar) geen LLM-calls
meer doen. We stubben daily_llm_tokens() hoog en verifiëren dat de job zacht
stopt zónder een regenerate/LLM aan te roepen.
"""
import asyncio
from unittest import mock

from backend.shared import outcomes
from backend.domains.publish import content_pipeline as cp
from backend.domains.seo import sites as sites_service
from backend.shared.config import CONTENT_MIN_SCORE


def test_require_llm_budget_raises_when_exceeded(monkeypatch):
    monkeypatch.setattr(outcomes, "daily_llm_tokens", lambda: 9_999_999)
    monkeypatch.setattr("backend.shared.config.DAILY_TOKEN_BUDGET", 1_000_000)
    try:
        outcomes.require_llm_budget("test")
        assert False, "verwacht BudgetExceeded"
    except outcomes.BudgetExceeded:
        pass


def test_content_improver_skips_when_budget_exceeded(monkeypatch):
    monkeypatch.setattr(outcomes, "daily_llm_tokens", lambda: 9_999_999)
    monkeypatch.setattr("backend.shared.config.DAILY_TOKEN_BUDGET", 1_000_000)

    calls = {"n": 0}
    async def fake_review(site, kw, html, max_rounds=6):
        calls["n"] += 1
        return html, {"score": 62, "feedback": "x"}
    cp.review_and_improve = fake_review

    async def run():
        return await cp.run_content_improver_job()
    summary = asyncio.run(run())
    assert summary.get("budget_exceeded") is True, summary
    assert calls["n"] == 0, "geen LLM-ronde bij lege budget"


def test_budget_not_exceeded_allows_run(monkeypatch):
    monkeypatch.setattr(outcomes, "daily_llm_tokens", lambda: 10)
    monkeypatch.setattr("backend.shared.config.DAILY_TOKEN_BUDGET", 1_000_000)
    # llm_budget_exceeded() kijkt óók naar een gedeelde quota-marker in de
    # database (een recente 403 van de provider, over de hele testsuite/het
    # hele proces heen) — zonder deze mock faalt deze test elke keer dat er
    # kort daarvoor ergens écht een 403 viel, wat niets zegt over de eigenlijke
    # dagbudget-logica die hier getest wordt.
    monkeypatch.setattr(outcomes, "llm_quota_backoff_active", lambda: False)
    # Mag geen exception gooien
    outcomes.require_llm_budget("test")
