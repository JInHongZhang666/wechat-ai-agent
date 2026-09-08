"""Five-dimension weighted scoring for article ranking.

Phase 3 replaces the simple engagement-based heat scoring with AIHOT's
five-dimension model. The model only evaluates each dimension — the
weighted formula and curation decision are handled by code.

Score = source_authority × 0.30 + content_novelty × 0.25
      + industry_impact × 0.25 + practical_value × 0.20

Design philosophy (from AIHOT V8+):
  "能用代码就别用模型" — LLM only scores dimensions, code makes decisions.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import datetime

from openai import OpenAI

from wechat_agent.collectors.base import SourceTier
from wechat_agent.config import LlmConfig
from wechat_agent.domain.models import (
    ArticleRecord,
    HeatConfidence,
    HeatScore,
)
from wechat_agent.scoring.llm_guard import (
    distribution_llm_available,
    record_distribution_llm_failure,
    reserve_distribution_llm_call,
)

logger = logging.getLogger(__name__)

# ── Default tier weights ─────────────────────────────────────────────

_TIER_WEIGHT: dict[SourceTier, float] = {
    SourceTier.T1: 1.0,
    SourceTier.T1_5: 0.85,
    SourceTier.T2: 0.65,
}

# ── Scoring prompts ──────────────────────────────────────────────────

_SCORING_SYSTEM = """\
你是一个 AI 资讯质量评分助手。对每条输入从以下三个维度独立打分（0-100）：

1. 内容新颖度 (novelty): 是否首次曝光、有新发现或独特视角
2. 行业影响度 (impact): 影响范围广、可能改变行业格局
3. 实用价值 (practicality): 对从业者的实际帮助程度

注意：你只需要打出三个分数，不要打总分，不要判断是否精选。

## 输出格式
严格输出 JSON:
{"scores": [{"id": "条目id", "novelty": 75, "impact": 80, "practicality": 60}]}
"""


def score_articles(
    articles: Sequence[ArticleRecord],
    now: datetime,
    *,
    llm_config: LlmConfig | None = None,
    batch_size: int = 10,
) -> dict[str, HeatScore]:
    """Score articles using the five-dimension formula.

    When LLM is available, it scores novelty/impact/practicality.
    Source authority comes from the tier system.
    When LLM is unavailable, falls back to deterministic scoring.
    """
    if not articles:
        return {}

    article_list = list(articles)

    if distribution_llm_available(llm_config):
        assert llm_config is not None
        return _llm_scoring(article_list, llm_config, now, batch_size)
    else:
        return _deterministic_scoring(article_list, now)


def _llm_scoring(
    articles: list[ArticleRecord],
    llm_config: LlmConfig,
    now: datetime,
    batch_size: int,
) -> dict[str, HeatScore]:
    """Use LLM to score novelty/impact/practicality, combine with tier authority."""
    client = OpenAI(
        api_key=llm_config.api_key,
        base_url=llm_config.base_url,
        timeout=llm_config.request_timeout,
    )
    scores: dict[str, dict[str, float]] = {}

    for i in range(0, len(articles), batch_size):
        batch = articles[i : i + batch_size]
        if not reserve_distribution_llm_call(llm_config):
            return _deterministic_scoring(articles, now)
        try:
            batch_scores = _score_batch(client, llm_config.scoring_model, batch)
            scores.update(batch_scores)
        except Exception as exc:
            logger.warning("Scoring batch %d failed: %s — using defaults", i, exc)
            if record_distribution_llm_failure(llm_config, exc) is not None:
                return _deterministic_scoring(articles, now)
            for article in batch:
                scores[article.id] = {"novelty": 50, "impact": 50, "practicality": 50}

    return _build_heat_scores(articles, scores, now)


def _score_batch(
    client: OpenAI,
    model: str,
    batch: list[ArticleRecord],
) -> dict[str, dict[str, float]]:
    items = [
        {
            "id": article.id,
            "title": article.title,
            "source": article.source_name,
        }
        for article in batch
    ]

    response = client.chat.completions.create(
        model=model,
        temperature=0.1,
        max_tokens=2048,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _SCORING_SYSTEM},
            {"role": "user", "content": json.dumps({"items": items}, ensure_ascii=False)},
        ],
    )

    content = response.choices[0].message.content
    if content is None:
        raise ValueError("Empty scoring response")

    data = json.loads(content)
    return {
        item["id"]: {
            "novelty": float(item["novelty"]),
            "impact": float(item["impact"]),
            "practicality": float(item["practicality"]),
        }
        for item in data.get("scores", [])
    }


def _build_heat_scores(
    articles: list[ArticleRecord],
    llm_scores: dict[str, dict[str, float]],
    now: datetime,
) -> dict[str, HeatScore]:
    """Combine tier-based authority weight with LLM dimension scores."""
    result: dict[str, HeatScore] = {}

    for article in articles:
        dims = llm_scores.get(
            article.id, {"novelty": 50, "impact": 50, "practicality": 50}
        )

        # Source authority from tier (or default T2)
        tier_str = article.tier or SourceTier.T2.value
        tier = SourceTier(tier_str) if tier_str in {t.value for t in SourceTier} else SourceTier.T2
        tier_weight = _TIER_WEIGHT.get(tier, 0.65)
        authority_score = tier_weight * 100  # Scale to 0-100

        # Five-dimension weighted formula:
        # Score = authority×0.30 + novelty×0.25 + impact×0.25 + practicality×0.20
        raw_score = (
            authority_score * 0.30
            + dims["novelty"] * 0.25
            + dims["impact"] * 0.25
            + dims["practicality"] * 0.20
        )

        components = {
            "authority": round(authority_score, 1),
            "novelty": round(dims["novelty"], 1),
            "impact": round(dims["impact"], 1),
            "practicality": round(dims["practicality"], 1),
        }

        result[article.id] = HeatScore(
            value=round(raw_score, 2),
            confidence=HeatConfidence.MEASURED,
            components=components,
        )

    return result


def _deterministic_scoring(
    articles: list[ArticleRecord],
    now: datetime,
) -> dict[str, HeatScore]:
    """Fallback deterministic scoring when no LLM is available.

    Uses the same weighted formula as LLM scoring with conservative fixed
    content dimensions and a small, bounded recency bonus. This keeps the
    offline path useful without letting T2 media pass solely for being new.
    """
    result: dict[str, HeatScore] = {}
    for article in articles:
        tier_str = article.tier or SourceTier.T2.value
        tier = SourceTier(tier_str) if tier_str in {t.value for t in SourceTier} else SourceTier.T2
        tier_weight = _TIER_WEIGHT.get(tier, 0.65)

        age_hours = max((now - article.published_at).total_seconds() / 3600, 0)
        authority = tier_weight * 100
        novelty = 55.0
        impact = 55.0
        practicality = 50.0
        recency_bonus = max(0.0, 4.0 * (1.0 - age_hours / 72.0))
        value = (
            authority * 0.30
            + novelty * 0.25
            + impact * 0.25
            + practicality * 0.20
            + recency_bonus
        )

        result[article.id] = HeatScore(
            value=round(min(value, 100), 2),
            confidence=HeatConfidence.ESTIMATED,
            components={
                "authority": round(authority, 1),
                "novelty": novelty,
                "impact": impact,
                "practicality": practicality,
                "recency_bonus": round(recency_bonus, 1),
            },
        )

    return result
