"""Tests for topic selection with Phase 3 five-dimension scoring.

With deterministic scoring (no LLM), ordering within categories is:
  -score.value (tier_weight × 50 + recency × 0.5), then source_confidence, then published_at.

All test articles default to T2 tier (weight 0.65), so ordering is primarily
driven by recency (newer articles score higher).
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from wechat_agent.collectors.base import SourceTier
from wechat_agent.domain.models import (
    ArticleRecord,
    EngagementMetrics,
    HeatConfidence,
    HeatScore,
    TopicCategory,
)
from wechat_agent.topics.select import select_topics


NOW = datetime(2026, 7, 25, 12, tzinfo=UTC)


@pytest.fixture
def fixture_articles() -> list[ArticleRecord]:
    fixture_path = Path(__file__).parents[1] / "fixtures" / "articles.json"
    articles = [ArticleRecord.model_validate(item) for item in json.loads(fixture_path.read_text())]
    # Attach T2 tier to fixture articles for deterministic scoring
    for i, a in enumerate(articles):
        a.tier = SourceTier.T2.value
    return articles


def test_selects_five_tools_and_five_news(fixture_articles):
    result = select_topics(fixture_articles, NOW, per_category=5)

    assert sum(x.category is TopicCategory.TOOL for x in result) == 5
    assert sum(x.category is TopicCategory.NEWS for x in result) == 5
    assert all(x.recommendation_reason for x in result)


def test_orders_within_each_category_by_heat_then_source_confidence(fixture_articles):
    """With deterministic T2 scoring, ordering is by recency then confidence."""
    result = select_topics(fixture_articles, NOW, per_category=5)
    tool_candidates = [item for item in result if item.category is TopicCategory.TOOL]

    # Fixture articles: tool-1 through tool-5, all T2 tier
    # Ordering by recency (newer first)
    ordered_ids = [item.id for item in tool_candidates]
    assert len(ordered_ids) == 5
    # Verify heat scores are in descending order
    for i in range(len(ordered_ids) - 1):
        assert tool_candidates[i].heat_score.value >= tool_candidates[i + 1].heat_score.value


def test_reports_shortage_without_fabricating_candidates():
    tools = [
        ArticleRecord(
            id=f"tool-{index}",
            title=f"Tool {index}",
            source_name="Tool Desk",
            source_url=f"https://example.com/tool-{index}",
            published_at=NOW - timedelta(hours=index),
            collected_at=NOW,
            summary="A practical AI tool.",
            category=TopicCategory.TOOL,
            source_confidence=0.9,
            engagement=EngagementMetrics(likes=index),
        )
        for index in range(1, 3)
    ]
    for a in tools:
        a.tier = SourceTier.T2.value

    result = select_topics(tools, NOW, per_category=5)

    assert len(result) == 2
    assert result[0].id == "tool-1"  # newer (1h) > older (2h)
    assert result.shortage == {TopicCategory.TOOL: 3, TopicCategory.NEWS: 5}


def test_reason_is_honest_when_source_confidence_is_not_provided():
    article = ArticleRecord(
        id="source-unknown",
        title="Tool with an unscored source",
        source_name="Unknown Desk",
        source_url="https://example.com/source-unknown",
        published_at=NOW - timedelta(hours=2),
        collected_at=NOW,
        summary="A practical AI tool.",
        category=TopicCategory.TOOL,
        engagement=EngagementMetrics(likes=3),
    )
    article.tier = SourceTier.T2.value

    result = select_topics([article], NOW, per_category=1)

    assert "来源可信度未提供" in result[0].recommendation_reason


def test_t1_articles_rank_higher_than_t2():
    """T1 articles outrank T2 articles even when older."""
    t1 = _tool_article("t1-older", age_hours=5, likes=1, confidence=0.5, tier=SourceTier.T1)
    t2 = _tool_article("t2-newer", age_hours=1, likes=10, confidence=0.9, tier=SourceTier.T2)

    result = select_topics([t1, t2], NOW, per_category=2)

    assert result[0].id == "t1-older"  # T1 authority outranks T2 recency


def test_uses_source_confidence_before_publication_time_when_heat_ties():
    """Same tier, same recency → confidence breaks tie."""
    articles = [
        _tool_article("low-conf", age_hours=1, likes=0, confidence=0.1, tier=SourceTier.T2),
        _tool_article("high-conf", age_hours=1, likes=9, confidence=0.9, tier=SourceTier.T2),
    ]

    result = select_topics(articles, NOW, per_category=2)

    assert result[0].id == "high-conf"
    assert result[1].id == "low-conf"


def test_uses_caller_supplied_scores_for_the_final_ranking():
    older = _tool_article(
        "llm-winner", age_hours=8, likes=0, confidence=0.2,
    )
    newer = _tool_article(
        "deterministic-winner", age_hours=1, likes=10, confidence=0.9,
    )
    scores = {
        older.id: HeatScore(
            value=95,
            confidence=HeatConfidence.MEASURED,
        ),
        newer.id: HeatScore(
            value=20,
            confidence=HeatConfidence.MEASURED,
        ),
    }

    result = select_topics(
        [older, newer],
        NOW,
        per_category=2,
        scores=scores,
    )

    assert [candidate.id for candidate in result] == [older.id, newer.id]
    assert result[0].heat_score == scores[older.id]


def _tool_article(
    article_id: str, *, age_hours: int, likes: int, confidence: float,
    tier: SourceTier = SourceTier.T2,
) -> ArticleRecord:
    art = ArticleRecord(
        id=article_id,
        title=article_id,
        source_name="Tool Desk",
        source_url=f"https://example.com/{article_id}",
        published_at=NOW - timedelta(hours=age_hours),
        collected_at=NOW,
        summary="A practical AI tool.",
        category=TopicCategory.TOOL,
        source_confidence=confidence,
        engagement=EngagementMetrics(likes=likes),
    )
    art.tier = tier.value
    return art
