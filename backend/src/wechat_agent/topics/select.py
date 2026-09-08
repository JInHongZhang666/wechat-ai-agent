"""Deterministic daily topic selection from ranked source articles."""

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime

from wechat_agent.domain.models import (
    ArticleRecord,
    HeatScore,
    TopicCandidate,
    TopicCategory,
)
from wechat_agent.scoring.heat import score_articles
from wechat_agent.topics.dedup import deduplicate


class TopicSelectionResult(list[TopicCandidate]):
    """Selected candidates with explicit counts for categories that fell short."""

    def __init__(
        self,
        candidates: Iterable[TopicCandidate] = (),
        *,
        shortage: Mapping[TopicCategory, int] | None = None,
    ) -> None:
        super().__init__(candidates)
        self.shortage = dict(shortage or {})


def select_topics(
    articles: Sequence[ArticleRecord],
    now: datetime,
    per_category: int = 5,
    *,
    scores: Mapping[str, HeatScore] | None = None,
) -> TopicSelectionResult:
    """Return top candidates per category without creating records to fill a gap."""
    if per_category < 1:
        raise ValueError("per_category must be at least 1")

    eligible_articles = deduplicate(articles)
    resolved_scores = (
        dict(scores)
        if scores is not None
        else score_articles(eligible_articles, now)
    )
    candidates: list[TopicCandidate] = []
    shortage: dict[TopicCategory, int] = {}

    # Only select from active categories (skip legacy if no articles)
    active_categories = _get_active_categories(eligible_articles)

    for category in active_categories:
        category_articles = [
            article for article in eligible_articles if article.category is category
        ]
        ranked_articles = sorted(
            category_articles,
            key=lambda article: (
                -resolved_scores[article.id].value,
                -(article.source_confidence if article.source_confidence is not None else -1),
                -article.published_at.timestamp(),
                article.id,
            ),
        )
        selected_articles = ranked_articles[:per_category]
        candidates.extend(
            _to_candidate(article, resolved_scores[article.id], now)
            for article in selected_articles
        )
        if len(selected_articles) < per_category:
            shortage[category] = per_category - len(selected_articles)

    return TopicSelectionResult(candidates, shortage=shortage)


def _get_active_categories(
    articles: Sequence[ArticleRecord],
) -> set[TopicCategory]:
    """Determine which categories to select from.

    Reports shortage for ALL categories, even empty ones.
    Uses the 5 new AIHOT categories when any articles use them;
    otherwise falls back to legacy tool/news.
    """
    cats_in_articles = {a.category for a in articles}

    new_cats = {
        TopicCategory.AI_MODELS, TopicCategory.AI_PRODUCTS,
        TopicCategory.INDUSTRY, TopicCategory.PAPER, TopicCategory.TIP,
    }
    legacy_cats = {TopicCategory.TOOL, TopicCategory.NEWS}

    has_new = bool(cats_in_articles & new_cats)

    if has_new:
        return new_cats | (cats_in_articles - new_cats)
    else:
        # Legacy mode — include both legacy categories for shortage reporting
        return legacy_cats | cats_in_articles


def _to_candidate(
    article: ArticleRecord, heat_score: HeatScore, now: datetime
) -> TopicCandidate:
    return TopicCandidate(
        id=article.id,
        title=article.title,
        category=article.category,
        heat_score=heat_score,
        summary=article.summary,
        recommendation_reason=_recommendation_reason(article, heat_score.value, now),
    )


def _recommendation_reason(
    article: ArticleRecord, heat_value: float, now: datetime
) -> str:
    age_hours = max((now - article.published_at).total_seconds() / 3600, 0)
    source_confidence = (
        f"{article.source_confidence:.2f}"
        if article.source_confidence is not None
        else "未提供"
    )
    return (
        f"发布后 {age_hours:.0f} 小时；同类热度 {heat_value:.2f}；"
        f"来源可信度{source_confidence}。"
    )
