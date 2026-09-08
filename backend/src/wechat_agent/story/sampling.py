"""Deterministic, suggestion-blind annotation sampling for Story pairs."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Literal

from pydantic import Field

from wechat_agent.domain.models import ArticleRecord, TopicCategory
from wechat_agent.story.models import (
    PairSuggestion,
    StoryCandidatePair,
    StoryModel,
)


SamplingStratum = Literal[
    "ambiguous",
    "hard_negative",
    "multi_publisher_positive",
    "coverage",
]


class StoryAnnotationArticle(StoryModel):
    id: str
    title: str
    summary: str
    source_name: str
    source_url: str
    publisher_group: str
    published_at: datetime
    category: TopicCategory


class StoryAnnotationCandidate(StoryModel):
    pair_id: str
    article_a: StoryAnnotationArticle
    article_b: StoryAnnotationArticle
    sampling_stratum: SamplingStratum = Field(exclude=True)


def _publisher_group(
    article: ArticleRecord,
    publisher_groups: Mapping[str, str],
) -> str:
    if article.source_id is not None:
        return publisher_groups.get(article.source_id, f"source:{article.source_id}")
    return article.source_identity or f"article:{article.id}"


def _strong_negative_signal(pair: StoryCandidatePair) -> bool:
    return pair.veto_reason is not None and any(
        pair.features.get(feature, 0.0) >= threshold
        for feature, threshold in (
            ("title_jaccard", 0.20),
            ("organization_overlap", 1.0),
            ("product_overlap", 1.0),
            ("identifier_match", 1.0),
        )
    )


def _information_key(pair: StoryCandidatePair) -> tuple[float, int, str]:
    uncertainty = 1.0 - abs(pair.score - 0.5) * 2
    feature_count = sum(value > 0 for value in pair.features.values())
    return (-uncertainty, -feature_count, pair.pair_key)


def _annotation_row(
    pair: StoryCandidatePair,
    articles: Mapping[str, ArticleRecord],
    publisher_groups: Mapping[str, str],
    stratum: SamplingStratum,
) -> StoryAnnotationCandidate:
    def article_payload(article_id: str) -> StoryAnnotationArticle:
        article = articles[article_id]
        return StoryAnnotationArticle(
            id=article.id,
            title=article.title,
            summary=article.summary[:500],
            source_name=article.source_name,
            source_url=article.source_url,
            publisher_group=_publisher_group(article, publisher_groups),
            published_at=article.published_at,
            category=article.category,
        )

    return StoryAnnotationCandidate(
        pair_id=pair.pair_key,
        article_a=article_payload(pair.article_a_id),
        article_b=article_payload(pair.article_b_id),
        sampling_stratum=stratum,
    )


def select_annotation_candidates(
    pairs: Iterable[StoryCandidatePair],
    articles: Iterable[ArticleRecord],
    *,
    limit: int = 50,
    publisher_groups: Mapping[str, str] | None = None,
) -> tuple[StoryAnnotationCandidate, ...]:
    """Select high-information rows while keeping algorithm answers private."""
    if not 1 <= limit <= 500:
        raise ValueError("annotation candidate limit must be between 1 and 500")
    articles_by_id = {article.id: article for article in articles}
    groups = publisher_groups or {}
    available = tuple(
        sorted(
            (
                pair
                for pair in pairs
                if pair.article_a_id in articles_by_id
                and pair.article_b_id in articles_by_id
            ),
            key=lambda pair: pair.pair_key,
        )
    )

    ambiguous = sorted(
        (pair for pair in available if pair.suggestion is PairSuggestion.AMBIGUOUS),
        key=lambda pair: (abs(pair.score - 0.70), pair.pair_key),
    )
    hard_negative = sorted(
        (pair for pair in available if _strong_negative_signal(pair)),
        key=lambda pair: (-pair.score, pair.pair_key),
    )
    multi_publisher_positive = sorted(
        (
            pair
            for pair in available
            if pair.suggestion is PairSuggestion.SAME
            and _publisher_group(articles_by_id[pair.article_a_id], groups)
            != _publisher_group(articles_by_id[pair.article_b_id], groups)
        ),
        key=lambda pair: (-pair.score, pair.pair_key),
    )

    selected: list[tuple[StoryCandidatePair, SamplingStratum]] = []
    selected_keys: set[str] = set()

    def take(
        pool: Iterable[StoryCandidatePair],
        count: int,
        stratum: SamplingStratum,
    ) -> None:
        for pair in pool:
            if len(selected) >= limit or count <= 0:
                return
            if pair.pair_key in selected_keys:
                continue
            selected.append((pair, stratum))
            selected_keys.add(pair.pair_key)
            count -= 1

    take(ambiguous, min(15, limit), "ambiguous")
    take(hard_negative, min(15, limit - len(selected)), "hard_negative")
    take(
        multi_publisher_positive,
        min(10, limit - len(selected)),
        "multi_publisher_positive",
    )

    coverage_by_category: dict[TopicCategory, list[StoryCandidatePair]] = defaultdict(list)
    for pair in available:
        if pair.pair_key not in selected_keys:
            coverage_by_category[articles_by_id[pair.article_a_id].category].append(pair)
    for values in coverage_by_category.values():
        values.sort(key=_information_key)
    balanced: list[StoryCandidatePair] = []
    while any(coverage_by_category.values()):
        for category in TopicCategory:
            values = coverage_by_category.get(category)
            if values:
                balanced.append(values.pop(0))
    take(balanced, min(10, limit - len(selected)), "coverage")

    remaining = sorted(
        (pair for pair in available if pair.pair_key not in selected_keys),
        key=_information_key,
    )
    take(remaining, limit - len(selected), "coverage")

    return tuple(
        _annotation_row(pair, articles_by_id, groups, stratum)
        for pair, stratum in selected
    )


def stratum_counts(
    rows: Iterable[StoryAnnotationCandidate],
) -> dict[str, int]:
    return dict(Counter(row.sampling_stratum for row in rows))
