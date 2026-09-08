from datetime import UTC, datetime

from wechat_agent.domain.models import ArticleRecord, TopicCategory
from wechat_agent.domain.relevance import (
    contains_relevance_keyword,
    is_distribution_eligible,
)


def test_ascii_ai_keyword_requires_a_real_token_boundary():
    assert contains_relevance_keyword("Google launches an AI agent") is True
    assert contains_relevance_keyword("Details said the game launches Friday") is False


def test_mixed_topic_source_requires_explicit_ai_relevance():
    article = ArticleRecord(
        id="general-news",
        title="普通游戏联动公告",
        source_name="IT之家",
        source_url="https://example.com/game",
        published_at=datetime(2026, 8, 2, tzinfo=UTC),
        collected_at=datetime(2026, 8, 2, tzinfo=UTC),
        summary="角色和关卡更新。",
        category=TopicCategory.INDUSTRY,
        source_identity="publisher:ithome",
        tier="t2",
    )

    assert is_distribution_eligible(article) is False
    assert is_distribution_eligible(
        article.model_copy(update={"title": "Gemini AI 智能体开放"})
    ) is True
