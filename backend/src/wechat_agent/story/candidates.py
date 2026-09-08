"""Explainable, deterministic Story candidate recall and scoring."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import re
import unicodedata
from typing import Iterable

from wechat_agent.domain.models import ArticleRecord
from wechat_agent.story.models import (
    STORY_RECALL_HOURS,
    STORY_STRATEGY_VERSION,
    NormalizedArticle,
    PairSuggestion,
    StoryCandidatePair,
)
from wechat_agent.story.normalize import normalize_article


FEATURE_WEIGHTS: dict[str, float] = {
    "title_jaccard": 0.22,
    "rare_token_overlap": 0.08,
    "organization_overlap": 0.10,
    "product_overlap": 0.12,
    "person_overlap": 0.04,
    "version_overlap": 0.10,
    "identifier_match": 0.18,
    "action_compatibility": 0.06,
    "time_proximity": 0.06,
    "publisher_independence": 0.02,
    "summary_overlap": 0.02,
}

HARD_VETOES: tuple[str, ...] = (
    "different_explicit_version",
    "different_identifier",
    "same_org_different_action",
    "same_topic_different_actor",
    "prediction_vs_occurrence",
    "overview_vs_specific_event",
    "different_time_or_place",
    "followup_new_event",
)

_GENERIC_TOKENS = frozenset(
    {"ai", "agent", "大模型", "模型", "人工智能", "发布", "正式", "新"}
)
_PREDICTION_MARKERS = (
    "或将",
    "将于",
    "预计",
    "传闻",
    "计划推出",
    "rumor",
    "expected to",
    "plans to",
    "may launch",
)
_OCCURRENCE_MARKERS = (
    "正式发布",
    "正式上线",
    "已经发布",
    "已发布",
    "宣布",
    "released",
    "launched",
)
_OVERVIEW_MARKERS = (
    "盘点",
    "教程",
    "指南",
    "回顾",
    "汇总",
    "overview",
    "tutorial",
    "guide",
)
_FOLLOWUP_ACTIONS = frozenset({"lawsuit", "regulation", "shutdown"})
_ORIGIN_ACTIONS = frozenset({"release", "open_source", "update"})
_LOCATIONS = (
    "北京",
    "上海",
    "深圳",
    "杭州",
    "纽约",
    "旧金山",
    "巴黎",
    "伦敦",
    "beijing",
    "shanghai",
    "shenzhen",
    "hangzhou",
    "new york",
    "san francisco",
    "paris",
    "london",
)
_WORD_RE = re.compile(r"[a-z0-9]+(?:\.[a-z0-9]+)*|[\u3400-\u4dbf\u4e00-\u9fff]{2,}")


@dataclass(frozen=True)
class CandidatePolicy:
    strategy_version: str = STORY_STRATEGY_VERSION
    minimum_recall_score: float = 0.20
    same_threshold: float = 0.70
    ambiguous_threshold: float = 0.30
    max_delta: timedelta = timedelta(hours=STORY_RECALL_HOURS)
    feature_weights: dict[str, float] = field(
        default_factory=lambda: dict(FEATURE_WEIGHTS)
    )


DEFAULT_POLICY = CandidatePolicy()


def _jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    left_set = frozenset(left)
    right_set = frozenset(right)
    if not left_set and not right_set:
        return 0.0
    union = left_set | right_set
    return len(left_set & right_set) / len(union)


def _shared(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    return _jaccard(left, right)


def _text(article: NormalizedArticle) -> str:
    return unicodedata.normalize(
        "NFKC", f"{article.article.title}\n{article.article.summary}"
    ).casefold()


def _words(text: str) -> frozenset[str]:
    return frozenset(_WORD_RE.findall(text))


def _locations(text: str) -> frozenset[str]:
    return frozenset(location for location in _LOCATIONS if location in text)


def _has_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _has_shared_event_anchor(
    left: NormalizedArticle, right: NormalizedArticle
) -> bool:
    return bool(
        left.products & right.products
        or left.organizations & right.organizations
        or left.identifiers & right.identifiers
        or left.versions & right.versions
    )


def _hard_veto(
    left: NormalizedArticle, right: NormalizedArticle
) -> str | None:
    left_text = _text(left)
    right_text = _text(right)

    if (
        left.identifiers
        and right.identifiers
        and left.identifiers.isdisjoint(right.identifiers)
    ):
        return "different_identifier"

    if (
        left.versions
        and right.versions
        and left.versions.isdisjoint(right.versions)
        and bool(left.products & right.products or left.organizations & right.organizations)
    ):
        return "different_explicit_version"

    left_followup = bool(left.action_codes & _FOLLOWUP_ACTIONS)
    right_followup = bool(right.action_codes & _FOLLOWUP_ACTIONS)
    left_origin = bool(left.action_codes & _ORIGIN_ACTIONS)
    right_origin = bool(right.action_codes & _ORIGIN_ACTIONS)
    if _has_shared_event_anchor(left, right) and (
        (left_followup and right_origin) or (right_followup and left_origin)
    ):
        return "followup_new_event"

    left_prediction = _has_any(left_text, _PREDICTION_MARKERS)
    right_prediction = _has_any(right_text, _PREDICTION_MARKERS)
    left_occurrence = _has_any(left_text, _OCCURRENCE_MARKERS)
    right_occurrence = _has_any(right_text, _OCCURRENCE_MARKERS)
    if _has_shared_event_anchor(left, right) and (
        (left_prediction and right_occurrence)
        or (right_prediction and left_occurrence)
    ):
        return "prediction_vs_occurrence"

    left_overview = _has_any(left_text, _OVERVIEW_MARKERS)
    right_overview = _has_any(right_text, _OVERVIEW_MARKERS)
    if _has_shared_event_anchor(left, right) and left_overview != right_overview:
        return "overview_vs_specific_event"

    left_locations = _locations(left_text)
    right_locations = _locations(right_text)
    if (
        _has_shared_event_anchor(left, right)
        and left_locations
        and right_locations
        and left_locations.isdisjoint(right_locations)
    ):
        return "different_time_or_place"

    if (
        left.organizations & right.organizations
        and left.action_codes
        and right.action_codes
        and left.action_codes.isdisjoint(right.action_codes)
    ):
        return "same_org_different_action"

    if (
        left.organizations
        and right.organizations
        and left.organizations.isdisjoint(right.organizations)
    ):
        shared_generic = _words(left_text) & _words(right_text) & _GENERIC_TOKENS
        if shared_generic:
            return "same_topic_different_actor"

    return None


def _feature_values(
    left: NormalizedArticle,
    right: NormalizedArticle,
    policy: CandidatePolicy,
) -> dict[str, float]:
    delta = abs((left.article.published_at - right.article.published_at).total_seconds())
    max_seconds = policy.max_delta.total_seconds()
    left_words = _words(_text(left)) - _GENERIC_TOKENS
    right_words = _words(_text(right)) - _GENERIC_TOKENS
    features = {
        "exact_title_match": float(
            bool(left.normalized_title)
            and left.normalized_title == right.normalized_title
        ),
        "title_jaccard": _jaccard(left.title_shingles, right.title_shingles),
        "rare_token_overlap": _jaccard(left_words, right_words),
        "organization_overlap": _shared(left.organizations, right.organizations),
        "product_overlap": _shared(left.products, right.products),
        "person_overlap": _shared(left.people, right.people),
        "version_overlap": _shared(left.versions, right.versions),
        "identifier_match": 1.0 if left.identifiers & right.identifiers else 0.0,
        "action_compatibility": _shared(left.action_codes, right.action_codes),
        "time_proximity": max(0.0, 1.0 - (delta / max_seconds)),
        "publisher_independence": float(
            (left.article.source_identity or left.article.source_id)
            != (right.article.source_identity or right.article.source_id)
        ),
        "summary_overlap": _jaccard(
            _words(left.article.summary.casefold()),
            _words(right.article.summary.casefold()),
        ),
    }
    return {name: round(value, 6) for name, value in features.items()}


def score_candidate_pair(
    left: NormalizedArticle,
    right: NormalizedArticle,
    now: datetime,
    policy: CandidatePolicy = DEFAULT_POLICY,
) -> StoryCandidatePair:
    features = _feature_values(left, right, policy)
    exact_url = left.normalized_url == right.normalized_url
    score = sum(
        features[name] * weight for name, weight in policy.feature_weights.items()
    )
    if exact_url:
        score = 1.0
    veto_reason = _hard_veto(left, right)
    if veto_reason is None and features["exact_title_match"] == 1.0:
        score = max(score, policy.same_threshold)
    score = round(min(1.0, max(0.0, score)), 6)
    if veto_reason is not None:
        suggestion = PairSuggestion.DIFFERENT
    elif score >= policy.same_threshold:
        suggestion = PairSuggestion.SAME
    elif score >= policy.ambiguous_threshold:
        suggestion = PairSuggestion.AMBIGUOUS
    else:
        suggestion = PairSuggestion.DIFFERENT
    return StoryCandidatePair.from_articles(
        left.article,
        right.article,
        now=now,
        features=features,
        score=score,
        suggestion=suggestion,
        veto_reason=veto_reason,
        strategy_version=policy.strategy_version,
    )


def _add_index(
    index: dict[str, set[str]], values: Iterable[str], article_id: str
) -> None:
    for value in values:
        index[value].add(article_id)


def recall_candidate_pairs(
    changed: Iterable[ArticleRecord],
    corpus: Iterable[ArticleRecord],
    now: datetime,
    policy: CandidatePolicy = DEFAULT_POLICY,
) -> tuple[StoryCandidatePair, ...]:
    """Recall bounded candidate pairs without enumerating the corpus square."""
    normalized_by_id = {
        article.id: normalize_article(article) for article in corpus
    }
    indexes: dict[str, dict[str, set[str]]] = {
        name: defaultdict(set)
        for name in ("url", "identifier", "organization", "product", "version", "shingle")
    }
    for article_id, normalized in normalized_by_id.items():
        _add_index(indexes["url"], (normalized.normalized_url,), article_id)
        _add_index(indexes["identifier"], normalized.identifiers, article_id)
        _add_index(indexes["organization"], normalized.organizations, article_id)
        _add_index(indexes["product"], normalized.products, article_id)
        _add_index(indexes["version"], normalized.versions, article_id)
        _add_index(indexes["shingle"], normalized.title_shingles, article_id)

    pairs_by_key: dict[str, StoryCandidatePair] = {}
    for article in changed:
        left = normalized_by_id.get(article.id)
        if left is None:
            continue
        candidate_ids: set[str] = set()
        lookup_values = {
            "url": (left.normalized_url,),
            "identifier": left.identifiers,
            "organization": left.organizations,
            "product": left.products,
            "version": left.versions,
            "shingle": left.title_shingles,
        }
        for name, values in lookup_values.items():
            for value in values:
                candidate_ids.update(indexes[name].get(value, ()))
        candidate_ids.discard(article.id)

        for candidate_id in candidate_ids:
            right = normalized_by_id[candidate_id]
            if abs(left.article.published_at - right.article.published_at) > policy.max_delta:
                continue
            pair = score_candidate_pair(left, right, now, policy)
            strong_anchor = bool(
                left.normalized_url == right.normalized_url
                or left.identifiers & right.identifiers
            )
            if (
                pair.veto_reason is None
                and not strong_anchor
                and pair.score < policy.minimum_recall_score
            ):
                continue
            pairs_by_key[pair.pair_key] = pair

    return tuple(
        sorted(pairs_by_key.values(), key=lambda pair: (-pair.score, pair.pair_key))
    )
