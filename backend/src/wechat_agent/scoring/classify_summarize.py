"""LLM-powered article classification and Chinese summary generation.

Uses an affordable model (DeepSeek V3.2 / deepseek-chat) to:
1. Classify each article into one of 5 AIHOT categories
2. Generate a concise Chinese summary (1-2 sentences)

Design philosophy (from AIHOT):
  Batch processing reduces API costs. The model only classifies and
  summarizes — curation decisions remain in code.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from openai import OpenAI

from wechat_agent.config import LlmConfig
from wechat_agent.domain.models import ArticleRecord, TopicCategory
from wechat_agent.scoring.llm_guard import (
    distribution_llm_available,
    record_distribution_llm_failure,
    reserve_distribution_llm_call,
)

logger = logging.getLogger(__name__)

# Category mapping for classification
_CATEGORY_LABELS = {
    "ai-models": "AI 模型发布/更新（新模型、权重、架构、训练方法）",
    "ai-products": "AI 产品发布/更新（工具、平台、API、SDK、应用）",
    "industry": "行业动态（政策、融资、合作、安全事件、行业趋势）",
    "paper": "论文研究（学术论文、技术报告、研究成果）",
    "tip": "技巧与观点（教程、最佳实践、个人观点、经验分享）",
}

_CLASSIFY_SYSTEM = """\
你是一个 AI 行业内容分类与摘要助手。

## 任务
1. 对每条输入判断所属分类（五选一）
2. 生成一句中文摘要（20-80字）

## 分类标准
- ai-models: 模型发布、新权重、架构变化、训练方法
- ai-products: 产品/工具/平台/API发布或更新
- industry: 行业事件、政策、融资、合作、安全问题
- paper: 学术论文、技术报告、研究
- tip: 教程、实践技巧、个人观点、经验

## 输出格式
严格 JSON: {"results": [{"id": "条目id", "category": "ai-models", "summary": "中文摘要"}]}
"""


def classify_and_summarize(
    articles: list[ArticleRecord],
    llm_config: LlmConfig,
    *,
    batch_size: int = 15,
) -> list[ArticleRecord]:
    """Classify and summarize articles using LLM.

    Returns updated ArticleRecord objects with LLM-generated summaries
    and categories. Falls back to original values on error.
    """
    if not articles or not distribution_llm_available(llm_config):
        return list(articles)

    client = OpenAI(
        api_key=llm_config.api_key,
        base_url=llm_config.base_url,
        timeout=llm_config.request_timeout,
    )

    results: dict[str, dict[str, str]] = {}

    for i in range(0, len(articles), batch_size):
        batch = articles[i : i + batch_size]
        if not reserve_distribution_llm_call(llm_config):
            break
        try:
            batch_results = _process_batch(client, llm_config.pre_screen_model, batch)
            results.update(batch_results)
        except Exception as exc:
            logger.warning("Classify batch %d failed: %s", i, exc)
            if record_distribution_llm_failure(llm_config, exc) is not None:
                break

    # Apply results to articles
    updated = []
    for article in articles:
        result = results.get(article.id)
        if result:
            new_cat = _parse_category(result.get("category", ""))
            updated.append(
                article.model_copy(update={
                    "category": new_cat,
                    "summary": result.get("summary", article.summary),
                })
            )
        else:
            updated.append(article)

    return updated


def _process_batch(
    client: OpenAI,
    model: str,
    batch: list[ArticleRecord],
) -> dict[str, dict[str, str]]:
    items = [
        {
            "id": a.id,
            "title": a.title,
            "source": a.source_name,
            "description": a.summary[:300],
        }
        for a in batch
    ]

    response = client.chat.completions.create(
        model=model,
        temperature=0.0,
        max_tokens=2048,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _CLASSIFY_SYSTEM},
            {"role": "user", "content": json.dumps({
                "categories": _CATEGORY_LABELS,
                "items": items,
            }, ensure_ascii=False)},
        ],
    )

    content = response.choices[0].message.content
    if content is None:
        raise ValueError("Empty classification response")

    data = json.loads(content)
    return {
        item["id"]: {
            "category": item.get("category", "industry"),
            "summary": item.get("summary", ""),
        }
        for item in data.get("results", [])
    }


def _parse_category(raw: str) -> TopicCategory:
    """Parse a raw category string to TopicCategory enum."""
    mapping = {
        "ai-models": TopicCategory.AI_MODELS,
        "ai-products": TopicCategory.AI_PRODUCTS,
        "industry": TopicCategory.INDUSTRY,
        "paper": TopicCategory.PAPER,
        "tip": TopicCategory.TIP,
        # Legacy
        "tool": TopicCategory.TOOL,
        "news": TopicCategory.NEWS,
    }
    return mapping.get(raw, TopicCategory.INDUSTRY)
