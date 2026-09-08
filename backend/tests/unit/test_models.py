from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from wechat_agent.domain.models import (
    ArticleRecord,
    CircuitState,
    EngagementMetrics,
    EvidenceFact,
    Source,
    SourceKind,
    SourceRun,
    SourceRunStatus,
    SourceTier,
    TopicCategory,
)


def test_article_requires_traceable_source_url():
    article = ArticleRecord(
        id="a1",
        title="一个 AI 工具",
        source_name="AI信息小屋测试源",
        source_url="https://mp.weixin.qq.com/s/example",
        published_at=datetime(2026, 7, 25, tzinfo=UTC),
        collected_at=datetime(2026, 7, 25, 1, tzinfo=UTC),
        summary="用于固定样本的摘要",
        category=TopicCategory.TOOL,
    )

    assert article.source_url.startswith("https://")
    assert article.category is TopicCategory.TOOL


def test_article_rejects_blank_source_url():
    with pytest.raises(ValidationError, match="source_url must not be empty"):
        ArticleRecord(
            id="a1",
            title="一个 AI 工具",
            source_name="AI信息小屋测试源",
            source_url="   ",
            published_at=datetime(2026, 7, 25, tzinfo=UTC),
            collected_at=datetime(2026, 7, 25, 1, tzinfo=UTC),
            summary="用于固定样本的摘要",
            category=TopicCategory.TOOL,
        )


def test_article_rejects_naive_datetime():
    with pytest.raises(ValidationError, match="datetime must be timezone-aware"):
        ArticleRecord(
            id="a1",
            title="一个 AI 工具",
            source_name="AI信息小屋测试源",
            source_url="https://mp.weixin.qq.com/s/example",
            published_at=datetime(2026, 7, 25),
            collected_at=datetime(2026, 7, 25, 1, tzinfo=UTC),
            summary="用于固定样本的摘要",
            category=TopicCategory.TOOL,
        )


def test_evidence_fact_rejects_blank_source_url():
    with pytest.raises(ValidationError, match="source_url must not be empty"):
        EvidenceFact(claim="可验证事实", source_name="来源", source_url="   ")


def test_likes_distinguishes_missing_from_measured_zero():
    assert EngagementMetrics().likes is None
    assert EngagementMetrics(likes=0).likes == 0


def test_source_requires_aware_operational_timestamps():
    with pytest.raises(ValidationError, match="datetime must be timezone-aware"):
        Source(
            id="official:example",
            name="Example",
            kind=SourceKind.RSS,
            tier=SourceTier.T1,
            publisher_group="official:example",
            categories=(TopicCategory.AI_MODELS,),
            circuit_state=CircuitState.CLOSED,
            created_at=datetime(2026, 8, 2),
            updated_at=datetime(2026, 8, 2, tzinfo=UTC),
        )


def test_source_run_cannot_finish_before_start():
    with pytest.raises(ValidationError, match="finished_at must not be earlier"):
        SourceRun(
            id="run-1",
            source_id="official:example",
            started_at=datetime(2026, 8, 2, 2, tzinfo=UTC),
            finished_at=datetime(2026, 8, 2, 1, tzinfo=UTC),
            status=SourceRunStatus.FAILED,
            duration_ms=1,
        )
