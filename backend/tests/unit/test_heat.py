"""Tests for the five-dimension scoring system (Phase 3).

The new scoring uses: authority×0.30 + novelty×0.25 + impact×0.25 + practicality×0.20
When no LLM is available, deterministic fallback uses the same formula with
conservative fixed content dimensions and a bounded recency bonus.
"""

from datetime import UTC, datetime, timedelta

import pytest

from wechat_agent.collectors.base import SourceTier
from wechat_agent.domain.models import (
    ArticleRecord,
    EngagementMetrics,
    HeatConfidence,
    TopicCategory,
)
from wechat_agent.scoring.heat import score_articles


NOW = datetime(2026, 7, 25, 12, tzinfo=UTC)


def article(
    article_id: str,
    *,
    published_at: datetime,
    likes: int | None,
    category: TopicCategory = TopicCategory.TOOL,
    views: int = 0,
    comments: int = 0,
    shares: int = 0,
    verified_proxy_signals: tuple[str, ...] = (),
    tier: SourceTier = SourceTier.T2,
) -> ArticleRecord:
    art = ArticleRecord(
        id=article_id,
        title=article_id,
        source_name="test source",
        source_url=f"https://example.com/{article_id}",
        published_at=published_at,
        collected_at=NOW,
        summary="test summary",
        category=category,
        engagement=EngagementMetrics(
            likes=likes,
            views=views,
            comments=comments,
            shares=shares,
            verified_proxy_signals=verified_proxy_signals,
        ),
    )
    art.tier = tier.value  # Set tier as model field
    return art


# ── Deterministic scoring tests (no LLM) ─────────────────────────────

def test_deterministic_scoring_produces_five_dimension_components():
    """Deterministic fallback returns authority/novelty/impact/practicality."""
    art = article("test", published_at=NOW - timedelta(hours=2), likes=10)
    scores = score_articles([art], NOW)

    assert "authority" in scores["test"].components
    assert "novelty" in scores["test"].components
    assert "impact" in scores["test"].components
    assert "practicality" in scores["test"].components


def test_deterministic_scoring_estimates_when_no_llm():
    """Without LLM config, confidence is ESTIMATED."""
    art = article("test", published_at=NOW - timedelta(hours=2), likes=10)
    scores = score_articles([art], NOW)

    assert scores["test"].confidence is HeatConfidence.ESTIMATED


def test_t2_default_tier_produces_moderate_score():
    """T2 remains below the publication threshold without measured quality."""
    art = article("t2-test", published_at=NOW - timedelta(hours=2), likes=10)
    scores = score_articles([art], NOW)

    score = scores["t2-test"]
    assert score.components["authority"] == pytest.approx(65.0, abs=0.1)
    assert score.components["novelty"] == 55.0
    assert score.components["impact"] == 55.0
    assert score.components["practicality"] == 50.0
    assert score.value < 70


def test_t1_tier_gets_higher_authority():
    """T1 authority is represented on the same 0-100 scale as other dimensions."""
    art = article("t1-test", published_at=NOW - timedelta(hours=2), likes=10, tier=SourceTier.T1)
    scores = score_articles([art], NOW)

    assert scores["t1-test"].components["authority"] == 100.0


def test_t1_5_tier_gets_intermediate_authority():
    """T1.5 authority remains between T1 and T2."""
    art = article("t15-test", published_at=NOW - timedelta(hours=2), likes=10, tier=SourceTier.T1_5)
    scores = score_articles([art], NOW)

    assert scores["t15-test"].components["authority"] == 85.0


def test_t1_scores_higher_than_t2_for_same_article():
    """T1 articles score higher than T2 for otherwise identical content."""
    t1 = article("t1", published_at=NOW - timedelta(hours=2), likes=10, tier=SourceTier.T1)
    t2 = article("t2", published_at=NOW - timedelta(hours=2), likes=10, tier=SourceTier.T2)
    scores = score_articles([t1, t2], NOW)

    assert scores["t1"].value > scores["t2"].value


def test_recency_decay_reduces_score_for_older_articles():
    """Older articles get lower recency bonus, reducing total score."""
    newer = article("newer", published_at=NOW - timedelta(hours=1), likes=10)
    older = article("older", published_at=NOW - timedelta(hours=50), likes=10)
    scores = score_articles([newer, older], NOW)

    assert scores["newer"].value >= scores["older"].value


def test_scoring_handles_empty_input():
    """Empty article list returns empty dict."""
    scores = score_articles([], NOW)
    assert scores == {}


def test_scoring_caps_value_at_100():
    """Score value is capped at 100."""
    art = article("max", published_at=NOW, likes=10000, tier=SourceTier.T1)
    scores = score_articles([art], NOW)
    assert scores["max"].value <= 100.0


def test_default_tier_when_not_set_is_t2():
    """Articles without explicit tier attribute default to T2."""
    art = ArticleRecord(
        id="no-tier",
        title="No Tier",
        source_name="test",
        source_url="https://example.com/no-tier",
        published_at=NOW - timedelta(hours=2),
        collected_at=NOW,
        summary="test",
        category=TopicCategory.TOOL,
    )
    scores = score_articles([art], NOW)
    assert scores["no-tier"].components["authority"] == pytest.approx(65.0, abs=0.1)
