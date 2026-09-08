"""Deterministic, code-owned curation decisions.

The model may score independent content dimensions, but it never decides
whether an item enters the curated feed.  This module applies transparent
source/category thresholds, a small title-quality adjustment and category
capacity limits.  The same functions are used by ingestion, backfill and
Gold-set replay so a decision can always be reproduced.
"""

from __future__ import annotations

from collections import defaultdict
import re

from wechat_agent.collectors.base import SourceTier
from wechat_agent.domain.models import (
    ArticleRecord,
    HeatScore,
    SourceType,
    TopicCategory,
)


_TIER_THRESHOLDS: dict[SourceTier, float] = {
    SourceTier.T1: 60.0,
    SourceTier.T1_5: 60.0,
    SourceTier.T2: 60.0,
}

_CATEGORY_ADJUSTMENTS: dict[TopicCategory, float] = {
    TopicCategory.TOOL: 0.0,
    TopicCategory.NEWS: -2.0,
    TopicCategory.AI_MODELS: -1.0,
    TopicCategory.AI_PRODUCTS: 0.0,
    TopicCategory.INDUSTRY: -2.0,
    TopicCategory.PAPER: 0.0,
    TopicCategory.TIP: 0.0,
}

DEFAULT_CURATED_PER_CATEGORY = 6
DEFAULT_CURATED_PAPERS = 3

_MAX_PER_CATEGORY: dict[TopicCategory, int] = {
    TopicCategory.TOOL: DEFAULT_CURATED_PER_CATEGORY,
    TopicCategory.NEWS: DEFAULT_CURATED_PER_CATEGORY,
    TopicCategory.AI_MODELS: DEFAULT_CURATED_PER_CATEGORY,
    TopicCategory.AI_PRODUCTS: DEFAULT_CURATED_PER_CATEGORY,
    TopicCategory.INDUSTRY: DEFAULT_CURATED_PER_CATEGORY,
    TopicCategory.PAPER: DEFAULT_CURATED_PAPERS,
    TopicCategory.TIP: DEFAULT_CURATED_PER_CATEGORY,
}

_AI_TOKEN = re.compile(r"(?<![a-z0-9])ai(?![a-z0-9])")
_VERSION_ONLY_TITLE = re.compile(
    r"[\w./-]+\s+v?\d+(?:\.\d+)+(?:[a-z]\d*)?",
)
_YEAR = re.compile(r"(?<!\d)(20\d{2})(?!\d)")

_AI_TITLE_TERMS = (
    "人工智能",
    "模型",
    "智能体",
    "agent",
    "gpt",
    "llm",
    "claude",
    "deepseek",
    "gemini",
    "copilot",
    "codex",
    "sora",
    "machine learning",
    "neural",
    "robot",
    "gpu",
    "inference",
    "encoder",
)
_CULTURE_TERMS = (
    "artist",
    "singer",
    "music",
    "song",
    "celebrity",
    "blogger",
    "billboard",
    "艺术家",
    "歌手",
    "音乐",
    "歌曲",
    "博主",
    "艺人",
    "排行榜",
    "致歉",
)
_EVENT_TERMS = (
    "competition",
    "contest",
    "award ceremony",
    "大赛",
    "决赛",
    "颁奖",
    "获奖之后",
)
_CONSUMER_TERMS = (
    "camera",
    "car ota",
    "摄像机",
    "汽车",
)
_SPECULATION_TERMS = ("rumor", "传闻", "有望")
_OFFICIAL_VALUE_TERMS = (
    "launch",
    "introduc",
    "release",
    "partner",
    "security",
    "safety",
    "model",
    "gpt",
    "chatgpt",
    "codex",
    "agent",
    "research",
    "science",
    "benchmark",
    "发布",
    "推出",
    "开源",
    "模型",
    "安全",
    "研究",
)

_PAPER_VALUE_TERMS: tuple[tuple[str, float], ...] = (
    ("tool", 2.0),
    ("repair", 2.0),
    ("editing", 2.0),
    ("pre-training", 2.0),
    ("fine-tuning", 1.0),
    ("reliability", 1.0),
    ("acquisition", 1.0),
    ("deployment", 1.0),
    ("inference", 1.0),
    ("dataset", 1.0),
    ("framework", 0.5),
)
_PAPER_NICHE_TERMS: tuple[tuple[str, float], ...] = (
    ("benchmarking", 1.5),
    ("formalism", 1.0),
    ("consensus", 1.0),
    ("reasoning", 1.0),
    ("measurement", 1.0),
    ("conjecture", 1.0),
    ("evaluation", 0.5),
)


def _normalized_title(article: ArticleRecord) -> str:
    return " ".join(article.title.casefold().split())


def _has_ai_title_signal(title: str) -> bool:
    return bool(_AI_TOKEN.search(title)) or any(
        term in title for term in _AI_TITLE_TERMS
    )


def _mentions_distant_future(article: ArticleRecord, title: str) -> bool:
    return any(
        int(match.group(1)) > article.published_at.year + 1
        for match in _YEAR.finditer(title)
    )


def get_quality_adjustment(article: ArticleRecord) -> float:
    """Return the explainable title-quality adjustment used for curation.

    The adjustment is intentionally small compared with the independent heat
    score.  It suppresses recurring low-value shapes found in the reviewed
    data and favours first-party, concrete announcements.  It is a ranking
    signal, not a replacement for model scoring.
    """
    title = _normalized_title(article)
    has_ai_signal = _has_ai_title_signal(title)
    adjustment = 0.0

    if article.category is not TopicCategory.PAPER and not has_ai_signal:
        adjustment -= 3.0

    if has_ai_signal and any(term in title for term in _CULTURE_TERMS):
        adjustment -= 5.0
    if any(term in title for term in _EVENT_TERMS):
        adjustment -= 5.0
    if has_ai_signal and (
        any(term in title for term in _CONSUMER_TERMS)
        or bool(re.search(r"(?<![a-z0-9])ota(?![a-z0-9])", title))
    ):
        adjustment -= 5.0
    if (
        any(term in title for term in _SPECULATION_TERMS)
        and _mentions_distant_future(article, title)
    ):
        adjustment -= 5.0
    if _VERSION_ONLY_TITLE.fullmatch(title) and not has_ai_signal:
        adjustment -= 3.0

    if (
        article.source_type is SourceType.OFFICIAL
        and any(term in title for term in _OFFICIAL_VALUE_TERMS)
    ):
        adjustment += 1.5

    if article.category is TopicCategory.PAPER:
        adjustment += sum(
            weight for term, weight in _PAPER_VALUE_TERMS if term in title
        )
        adjustment -= sum(
            weight for term, weight in _PAPER_NICHE_TERMS if term in title
        )

    return round(adjustment, 2)


def get_curation_priority(article: ArticleRecord, score: HeatScore) -> float:
    """Combine the independent heat score and transparent quality adjustment."""
    return round(score.value + get_quality_adjustment(article), 2)


def _source_tier(article: ArticleRecord) -> SourceTier:
    tier_str = article.tier or SourceTier.T2.value
    if tier_str not in {tier.value for tier in SourceTier}:
        return SourceTier.T2
    return SourceTier(tier_str)


def get_threshold_for(article: ArticleRecord) -> float:
    """Return the effective curation threshold for an article."""
    base = _TIER_THRESHOLDS.get(_source_tier(article), 60.0)
    return base + _CATEGORY_ADJUSTMENTS.get(article.category, 0.0)


def get_category_limit(
    category: TopicCategory,
    *,
    override: int | None = None,
) -> int:
    """Return the per-run capacity for a category."""
    if override is not None:
        return override
    return _MAX_PER_CATEGORY.get(category, DEFAULT_CURATED_PER_CATEGORY)


def is_curated(article: ArticleRecord, score: HeatScore) -> bool:
    """Return whether an article clears the deterministic quality threshold."""
    return get_curation_priority(article, score) >= get_threshold_for(article)


def explain_curation(article: ArticleRecord, score: HeatScore) -> str:
    """Return a human-readable explanation of the threshold decision."""
    tier_names = {
        SourceTier.T1: "一手官方",
        SourceTier.T1_5: "权威媒体",
        SourceTier.T2: "社区/媒体",
    }
    tier = _source_tier(article)
    threshold = get_threshold_for(article)
    adjustment = get_quality_adjustment(article)
    priority = get_curation_priority(article, score)
    passed = priority >= threshold
    scoring_method = (
        "维度实测" if score.confidence.value == "measured" else "离线估算"
    )
    result = "达到精选门槛" if passed else "未达精选门槛"
    return (
        f"信源等级: {tier_names[tier]} (门槛 {threshold:.0f} 分), "
        f"原始得分: {score.value:.1f} 分 ({scoring_method}), "
        f"内容调整: {adjustment:+.1f}, 精选优先分: {priority:.1f}, "
        f"结果: {result}"
    )


def select_curated(
    articles: list[ArticleRecord],
    scores: dict[str, HeatScore],
    *,
    per_category: int | None = None,
) -> list[ArticleRecord]:
    """Select the highest-priority eligible items within each category cap."""
    by_category: dict[
        TopicCategory,
        list[tuple[ArticleRecord, float]],
    ] = defaultdict(list)

    for article in articles:
        score = scores.get(article.id)
        if score is None or not is_curated(article, score):
            continue
        by_category[article.category].append(
            (article, get_curation_priority(article, score))
        )

    selected: list[ArticleRecord] = []
    for category, scored_articles in by_category.items():
        scored_articles.sort(key=lambda item: item[1], reverse=True)
        limit = get_category_limit(category, override=per_category)
        selected.extend(article for article, _ in scored_articles[:limit])

    return selected
