"""Constraint-safe deterministic clustering for Story shadow candidates."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Mapping

from wechat_agent.domain.models import ArticleRecord, SourceType
from wechat_agent.story.candidates import DEFAULT_POLICY, HARD_VETOES, score_candidate_pair
from wechat_agent.story.models import (
    STORY_SETTLE_HOURS,
    NormalizedArticle,
    PairSuggestion,
    ProvisionalCluster,
    StoryCandidatePair,
    StoryStatus,
)
from wechat_agent.story.normalize import normalize_article


_TIER_RANK = {"t1": 3, "t1_5": 2, "t2": 1}


def primary_rank(article: ArticleRecord) -> tuple[int, int, float, float, str]:
    """Rank evidence quality before recency, with an ID tie-breaker."""
    return (
        1 if article.source_type is SourceType.OFFICIAL else 0,
        _TIER_RANK.get(article.tier or "", 0),
        article.source_confidence or 0.0,
        -article.published_at.timestamp(),
        article.id,
    )


def choose_primary_article(articles: Iterable[ArticleRecord]) -> ArticleRecord:
    candidates = tuple(articles)
    if not candidates:
        raise ValueError("cannot choose a primary article from an empty cluster")
    return max(candidates, key=primary_rank)


def cluster_is_compatible(
    left_members: Iterable[NormalizedArticle],
    right_members: Iterable[NormalizedArticle],
) -> tuple[bool, str | None]:
    """Check every cross-component pair so transitive edges cannot bridge vetoes."""
    reasons: set[str] = set()
    left_sorted = sorted(left_members, key=lambda item: item.article.id)
    right_sorted = sorted(right_members, key=lambda item: item.article.id)
    for left in left_sorted:
        for right in right_sorted:
            generated_at = max(left.article.collected_at, right.article.collected_at)
            reason = score_candidate_pair(
                left, right, generated_at, DEFAULT_POLICY
            ).veto_reason
            if reason is not None:
                reasons.add(reason)
    if not reasons:
        return True, None
    order = {reason: index for index, reason in enumerate(HARD_VETOES)}
    return False, min(reasons, key=lambda reason: (order.get(reason, 999), reason))


def resolve_story_status(
    latest_at: datetime,
    now: datetime,
    *,
    merged: bool = False,
) -> StoryStatus:
    if merged:
        return StoryStatus.MERGED
    if now - latest_at >= timedelta(hours=STORY_SETTLE_HOURS):
        return StoryStatus.SETTLED
    return StoryStatus.ACTIVE


def _source_identity(article: ArticleRecord) -> str:
    return article.source_id or article.source_identity or f"article:{article.id}"


def _publisher_group(
    article: ArticleRecord, publisher_groups: Mapping[str, str]
) -> str:
    if article.source_id is not None:
        return publisher_groups.get(article.source_id, f"source:{article.source_id}")
    return article.source_identity or f"article:{article.id}"


def build_provisional_clusters(
    articles: Iterable[ArticleRecord],
    pairs: Iterable[StoryCandidatePair],
    now: datetime,
    publisher_groups: Mapping[str, str],
) -> tuple[ProvisionalCluster, ...]:
    """Build stable components while enforcing hard vetoes across whole clusters."""
    del now  # The caller-owned anchor is reserved for lifecycle projection.
    articles_by_id = {article.id: article for article in articles}
    normalized_by_id = {
        article_id: normalize_article(article)
        for article_id, article in articles_by_id.items()
    }
    parent = {article_id: article_id for article_id in articles_by_id}
    vetoes_by_article: dict[str, set[str]] = {
        article_id: set() for article_id in articles_by_id
    }

    def find(article_id: str) -> str:
        root = article_id
        while parent[root] != root:
            root = parent[root]
        while parent[article_id] != article_id:
            next_id = parent[article_id]
            parent[article_id] = root
            article_id = next_id
        return root

    def member_ids(root: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                article_id
                for article_id in articles_by_id
                if find(article_id) == root
            )
        )

    eligible_edges = sorted(
        (
            pair
            for pair in pairs
            if pair.suggestion is PairSuggestion.SAME
            and pair.article_a_id in articles_by_id
            and pair.article_b_id in articles_by_id
        ),
        key=lambda pair: (-pair.score, pair.pair_key),
    )
    for pair in eligible_edges:
        left_root = find(pair.article_a_id)
        right_root = find(pair.article_b_id)
        if left_root == right_root:
            continue
        left_ids = member_ids(left_root)
        right_ids = member_ids(right_root)
        compatible, reason = cluster_is_compatible(
            (normalized_by_id[article_id] for article_id in left_ids),
            (normalized_by_id[article_id] for article_id in right_ids),
        )
        if not compatible:
            assert reason is not None
            for article_id in (*left_ids, *right_ids):
                vetoes_by_article[article_id].add(reason)
            continue
        winner, loser = sorted((left_root, right_root))
        parent[loser] = winner

    grouped: dict[str, list[ArticleRecord]] = {}
    for article_id in sorted(articles_by_id):
        grouped.setdefault(find(article_id), []).append(articles_by_id[article_id])

    clusters: list[ProvisionalCluster] = []
    for members in grouped.values():
        members = sorted(members, key=lambda article: article.id)
        primary = choose_primary_article(members)
        source_ids = {_source_identity(article) for article in members}
        group_ids = {
            _publisher_group(article, publisher_groups) for article in members
        }
        clusters.append(
            ProvisionalCluster(
                article_ids=tuple(article.id for article in members),
                primary_article_id=primary.id,
                first_seen_at=min(article.published_at for article in members),
                latest_at=max(article.published_at for article in members),
                source_count=len(source_ids),
                publisher_group_count=len(group_ids),
                signal_count=len(group_ids),
                veto_reasons=tuple(
                    sorted(
                        {
                            reason
                            for article in members
                            for reason in vetoes_by_article[article.id]
                        }
                    )
                ),
            )
        )
    return tuple(
        sorted(
            clusters,
            key=lambda cluster: (cluster.first_seen_at, cluster.primary_article_id),
        )
    )
