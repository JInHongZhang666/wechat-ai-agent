from datetime import UTC, datetime, timedelta

from wechat_agent.api.v1_routes import (
    DistributionRow,
    _build_hot_topics,
    _item_sort_at,
)
from wechat_agent.domain.models import (
    ArticleRecord,
    CurationDecision,
    HeatConfidence,
    TopicCategory,
)


NOW = datetime(2026, 8, 2, 6, tzinfo=UTC)


def _row(article_id: str, source_id: str, source_name: str, title: str):
    return DistributionRow(
        article=ArticleRecord(
            id=article_id,
            source_id=source_id,
            title=title,
            source_name=source_name,
            source_url=f"https://example.com/{article_id}",
            published_at=NOW,
            collected_at=NOW,
            summary="One report about the same verified event.",
            category=TopicCategory.AI_MODELS,
            tier="t1",
        ),
        decision=CurationDecision(
            article_id=article_id,
            distribution_score=90,
            selected=True,
            eligible=True,
            threshold=60,
            reason="Selected for test.",
            score_confidence=HeatConfidence.MEASURED,
            strategy_version="distribution-v3",
            scored_at=NOW,
        ),
    )


def test_hot_topic_requires_two_independent_publisher_groups():
    same_operator = [
        _row("a", "source:a", "Source A", "GPT-6 正式发布！"),
        _row("b", "source:b", "Source B", "GPT-6 正式发布"),
    ]
    publisher_groups = {
        "source:a": "publisher:one",
        "source:b": "publisher:one",
        "source:c": "publisher:two",
    }

    assert _build_hot_topics(
        same_operator,
        publisher_groups=publisher_groups,
    ) == []

    topics = _build_hot_topics(
        [
            *same_operator,
            _row("c", "source:c", "Source C", "ＧＰＴ-6 正式发布"),
        ],
        publisher_groups=publisher_groups,
    )

    assert len(topics) == 1
    assert topics[0]["sourceCount"] == 3
    assert topics[0]["signalCount"] == 2
    assert topics[0]["publisherGroupCount"] == 2
    assert topics[0]["reportCount"] == 3
    assert topics[0]["qualification"] == "multi_publisher"


def test_timeline_uses_discovery_for_recent_items_but_not_old_backfills():
    recent = _row("recent", "source:a", "Source A", "Recent").article
    recent = recent.model_copy(
        update={
            "published_at": NOW - timedelta(hours=1),
            "collected_at": NOW,
        }
    )
    backfill = recent.model_copy(
        update={
            "id": "backfill",
            "published_at": NOW - timedelta(days=4),
            "collected_at": NOW,
        }
    )

    assert _item_sort_at(recent, by="timeline", anchor=NOW) == NOW
    assert _item_sort_at(backfill, by="timeline", anchor=NOW) == backfill.published_at
    assert _item_sort_at(recent, by="published", anchor=NOW) == recent.published_at
