"""Two-pass AI pre-screening for article relevance and quality.

Phase 1 pre-screen: Uses an affordable model (DeepSeek V3.2 / deepseek-chat)
to filter out pure marketing, duplicate reprints, and non-AI content.
Target pass rate: ~30% of raw inputs.

Design philosophy (from AIHOT V8+):
  "能用代码就别用模型" — The model only classifies yes/no, it does NOT
  make curation decisions. All thresholds and rules live in curation.py.
"""

from __future__ import annotations

import json
import logging

from openai import OpenAI

from wechat_agent.config import LlmConfig
from wechat_agent.domain.models import ArticleRecord
from wechat_agent.scoring.llm_guard import (
    distribution_llm_available,
    record_distribution_llm_failure,
    reserve_distribution_llm_call,
)

logger = logging.getLogger(__name__)

# ── Pre-screen prompt (minimal, focused) ──────────────────────────────

_PRE_SCREEN_SYSTEM = """\
你是一个 AI 内容过滤器。对每条输入判断是否属于 AI 行业相关信息。

## 过滤规则
- 通过 (pass): AI 模型发布、AI 产品发布、AI 行业事件、AI 论文、AI 教程与观点
- 拒绝 (reject): 纯营销/PR稿/广告、重复转载、与 AI 无关的科技新闻、纯融资消息

## 输出格式
严格输出 JSON:
{"results": [{"id": "条目id", "pass": true/false, "reason": "一句话原因"}]}
"""


def pre_screen_articles(
    articles: list[ArticleRecord],
    llm_config: LlmConfig,
    *,
    batch_size: int = 20,
) -> list[ArticleRecord]:
    """Filter articles through an affordable LLM pre-screen pass.

    Returns only articles that pass the relevance filter.
    Falls back to returning all articles if LLM is unavailable.
    """
    if not articles:
        return []

    if not distribution_llm_available(llm_config):
        logger.warning("LLM not available — skipping pre-screen, keeping all %d articles", len(articles))
        return list(articles)

    client = OpenAI(
        api_key=llm_config.api_key,
        base_url=llm_config.base_url,
        timeout=llm_config.request_timeout,
    )

    passed: list[ArticleRecord] = []

    for i in range(0, len(articles), batch_size):
        batch = articles[i : i + batch_size]
        if not reserve_distribution_llm_call(llm_config):
            passed.extend(articles[i:])
            break
        try:
            batch_passed = _screen_batch(client, llm_config.pre_screen_model, batch)
            passed.extend(batch_passed)
        except Exception as exc:
            logger.warning("Pre-screen batch %d failed: %s — keeping all in batch", i, exc)
            passed.extend(batch)
            if record_distribution_llm_failure(llm_config, exc) is not None:
                passed.extend(articles[i + batch_size :])
                break

    pass_rate = len(passed) / len(articles) * 100 if articles else 0
    logger.info(
        "Pre-screen: %d/%d passed (%.0f%%)",
        len(passed), len(articles), pass_rate,
    )
    return passed


def _screen_batch(
    client: OpenAI,
    model: str,
    batch: list[ArticleRecord],
) -> list[ArticleRecord]:
    """Screen a single batch of articles."""
    items = [
        {
            "id": article.id,
            "title": article.title,
            "source": article.source_name,
            "summary": article.summary[:200],
        }
        for article in batch
    ]

    response = client.chat.completions.create(
        model=model,
        temperature=0.0,
        max_tokens=1024,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _PRE_SCREEN_SYSTEM},
            {"role": "user", "content": json.dumps({"items": items}, ensure_ascii=False)},
        ],
    )

    content = response.choices[0].message.content
    if content is None:
        raise ValueError("Empty pre-screen response")

    data = json.loads(content)
    results = data.get("results", [])
    passed_ids = {
        item["id"]
        for item in results
        if item.get("pass", False)
    }

    return [article for article in batch if article.id in passed_ids]


def pre_screen_sync(
    articles: list[ArticleRecord],
    llm_config: LlmConfig | None,
    *,
    batch_size: int = 20,
) -> list[ArticleRecord]:
    """Synchronous wrapper — returns all articles when no LLM config."""
    if llm_config is None:
        return list(articles)
    return pre_screen_articles(articles, llm_config, batch_size=batch_size)
