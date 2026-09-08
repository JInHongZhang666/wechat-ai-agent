"""Deterministic builders shared by Story unit and integration tests."""

from datetime import UTC, datetime

from wechat_agent.domain.models import ArticleRecord, SourceType, TopicCategory
from wechat_agent.story.models import PairSuggestion, StoryCandidatePair


NOW = datetime(2026, 8, 19, 8, 0, tzinfo=UTC)


def make_story_article(**overrides: object) -> ArticleRecord:
    values: dict[str, object] = {
        "id": "article-1",
        "source_id": "publisher:test",
        "title": "Qwen 3.5 正式发布",
        "source_name": "Test Publisher",
        "source_url": "https://example.com/article-1",
        "published_at": NOW,
        "collected_at": NOW,
        "summary": "Qwen 3.5 model release.",
        "category": TopicCategory.AI_MODELS,
        "source_confidence": 0.8,
        "source_type": SourceType.SECONDARY,
        "source_identity": "publisher:test",
        "source_type_verified": True,
        "tier": "t2",
    }
    values.update(overrides)
    if "id" in overrides and "source_url" not in overrides:
        values["source_url"] = f"https://example.com/{overrides['id']}"
    if "title" in overrides and "summary" not in overrides:
        values["summary"] = str(overrides["title"])
    return ArticleRecord.model_validate(values)


def make_candidate_pair(
    left: ArticleRecord,
    right: ArticleRecord,
    *,
    score: float,
    suggestion: PairSuggestion = PairSuggestion.SAME,
    veto_reason: str | None = None,
) -> StoryCandidatePair:
    return StoryCandidatePair.from_articles(
        left,
        right,
        now=NOW,
        score=score,
        suggestion=suggestion,
        veto_reason=veto_reason,
    )
