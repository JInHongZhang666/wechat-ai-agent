from datetime import UTC, datetime

import pytest

from wechat_agent.config import LlmConfig
from wechat_agent.domain.models import ArticleRecord, HeatConfidence, TopicCategory
from wechat_agent.scoring import classify_summarize, heat, pre_screen
from wechat_agent.scoring.llm_guard import (
    distribution_llm_available,
    distribution_llm_snapshot,
    record_distribution_llm_failure,
    reserve_distribution_llm_call,
    reset_distribution_llm_circuit,
    reset_distribution_llm_guard_for_tests,
)


NOW = datetime(2026, 8, 18, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _reset_guard():
    reset_distribution_llm_guard_for_tests()
    yield
    reset_distribution_llm_guard_for_tests()


def _config(**updates) -> LlmConfig:
    values = {
        "api_key": "test-key",
        "base_url": "https://guard.test/v1",
        "pre_screen_model": "cheap-model",
        "scoring_model": "scoring-model",
        "distribution_daily_call_limit": 10,
    }
    values.update(updates)
    return LlmConfig(**values)


def _article() -> ArticleRecord:
    return ArticleRecord(
        id="article-1",
        title="AI model release",
        source_name="Official",
        source_url="https://example.com/article-1",
        published_at=NOW,
        collected_at=NOW,
        summary="An AI model was released.",
        category=TopicCategory.AI_MODELS,
        tier="t1",
    )


def test_distribution_guard_enforces_daily_call_budget():
    config = _config(distribution_daily_call_limit=2)

    assert reserve_distribution_llm_call(config) is True
    assert reserve_distribution_llm_call(config) is True
    assert reserve_distribution_llm_call(config) is False

    snapshot = distribution_llm_snapshot(config)
    assert snapshot["available"] is False
    assert snapshot["callsToday"] == 2
    assert snapshot["reason"] == "LLM_DAILY_BUDGET_EXHAUSTED"


@pytest.mark.parametrize(
    ("status_code", "message", "expected"),
    [
        (401, "Authentication failed", "LLM_AUTH_FAILED"),
        (402, "Insufficient Balance", "LLM_INSUFFICIENT_BALANCE"),
    ],
)
def test_distribution_guard_opens_and_resets_permanent_error_circuit(
    status_code,
    message,
    expected,
):
    config = _config()
    error = RuntimeError(message)
    error.status_code = status_code

    assert record_distribution_llm_failure(config, error) == expected
    assert distribution_llm_available(config) is False
    assert distribution_llm_snapshot(config)["reason"] == expected

    reset_distribution_llm_circuit(config)
    assert distribution_llm_available(config) is True


def test_distribution_stages_use_their_specialized_models(monkeypatch):
    config = _config()
    article = _article()
    used_models = []

    def screen_batch(_client, model, batch):
        used_models.append(("screen", model))
        return batch

    def classify_batch(_client, model, batch):
        used_models.append(("classify", model))
        return {
            item.id: {"category": "ai-models", "summary": "中文摘要"}
            for item in batch
        }

    def score_batch(_client, model, batch):
        used_models.append(("score", model))
        return {
            item.id: {"novelty": 80, "impact": 75, "practicality": 70}
            for item in batch
        }

    monkeypatch.setattr(pre_screen, "_screen_batch", screen_batch)
    monkeypatch.setattr(classify_summarize, "_process_batch", classify_batch)
    monkeypatch.setattr(heat, "_score_batch", score_batch)

    screened = pre_screen.pre_screen_articles([article], config)
    classified = classify_summarize.classify_and_summarize(screened, config)
    scores = heat.score_articles(classified, NOW, llm_config=config)

    assert used_models == [
        ("screen", "cheap-model"),
        ("classify", "cheap-model"),
        ("score", "scoring-model"),
    ]
    assert scores[article.id].confidence is HeatConfidence.MEASURED


def test_402_in_pre_screen_stops_later_distribution_calls(monkeypatch):
    config = _config()
    article = _article()

    class PaymentRequiredError(RuntimeError):
        status_code = 402

    def fail_screen(*_args):
        raise PaymentRequiredError("Insufficient Balance")

    classify_call = pytest.fail
    score_call = pytest.fail
    monkeypatch.setattr(pre_screen, "_screen_batch", fail_screen)
    monkeypatch.setattr(classify_summarize, "_process_batch", classify_call)
    monkeypatch.setattr(heat, "_score_batch", score_call)

    screened = pre_screen.pre_screen_articles([article], config)
    classified = classify_summarize.classify_and_summarize(screened, config)
    scores = heat.score_articles(classified, NOW, llm_config=config)

    assert screened == [article]
    assert classified == [article]
    assert distribution_llm_snapshot(config)["reason"] == "LLM_INSUFFICIENT_BALANCE"
    assert scores[article.id].confidence is HeatConfidence.ESTIMATED
