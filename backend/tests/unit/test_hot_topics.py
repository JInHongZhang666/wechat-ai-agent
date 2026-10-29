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


def _row(
    article_id: str,
    source_id: str,
    source_name: str,
    title: str,
    *,
    selected: bool = True,
    source_identity: str | None = None,
):
    return DistributionRow(
        article=ArticleRecord(
            id=article_id,
            source_id=source_id,
            title=title,
            source_name=source_name,
            source_url=f"https://example.com/{article_id}",
            source_identity=source_identity,
            published_at=NOW,
            collected_at=NOW,
            summary="One report about the same verified event.",
            category=TopicCategory.AI_MODELS,
            tier="t1",
        ),
        decision=CurationDecision(
            article_id=article_id,
            distribution_score=90,
            selected=selected,
            eligible=True,
            threshold=60,
            reason="Selected for test.",
            score_confidence=HeatConfidence.MEASURED,
            strategy_version="distribution-v3",
            scored_at=NOW,
        ),
    )


def test_hot_topic_counts_reports_and_independent_publisher_groups():
    same_operator = [
        _row("a", "source:a", "Source A", "GPT-6 正式发布！"),
        _row("b", "source:b", "Source B", "GPT-6 正式发布"),
    ]
    publisher_groups = {
        "source:a": "publisher:one",
        "source:b": "publisher:one",
        "source:c": "publisher:two",
    }

    single_publisher_topics = _build_hot_topics(
        same_operator,
        publisher_groups=publisher_groups,
    )
    assert len(single_publisher_topics) == 1
    assert single_publisher_topics[0]["reportCount"] == 2
    assert single_publisher_topics[0]["qualification"] == "single_source"

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


def test_hot_topic_groups_related_headlines_by_recurring_core_subject():
    topics = _build_hot_topics(
        [
            _row("a", "source:a", "Source A", "OpenAI 发布 GPT-6.1 Terra"),
            _row(
                "b",
                "source:b",
                "Source B",
                "OpenClaw 已支持 GPT-6 Terra",
                selected=False,
            ),
            _row("c", "source:c", "Source C", "DeepSeek 发布全新推理模型"),
        ],
        publisher_groups={
            "source:a": "publisher:a",
            "source:b": "publisher:b",
            "source:c": "publisher:c",
        },
    )

    assert topics[0]["reportCount"] == 2
    assert topics[0]["sourceCount"] == 2
    assert topics[0]["publisherGroupCount"] == 2
    assert topics[0]["qualification"] == "multi_publisher"
    assert topics[1]["reportCount"] == 1


def test_hot_topic_counts_accounts_on_shared_platform_as_distinct_sources():
    topics = _build_hot_topics([
        _row(
            "a",
            "aggregator:aihot",
            "X：Account A (@a)",
            "OpenAI 发布 GPT-6.1 Terra",
            source_identity="external:x.com",
        ),
        _row(
            "b",
            "aggregator:aihot",
            "X：Account B (@b)",
            "OpenClaw 已支持 GPT-6 Terra",
            selected=False,
            source_identity="external:x.com",
        ),
    ])

    assert topics[0]["reportCount"] == 2
    assert topics[0]["sourceCount"] == 2
    assert topics[0]["publisherGroupCount"] == 2


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
