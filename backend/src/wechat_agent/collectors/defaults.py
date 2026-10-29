"""Default public article sources for the live topic radar.

Phase 5 expands to 30+ curated RSS sources across 5 AIHOT-compatible
categories. Each source is tiered (T1/T1_5/T2) for the five-dimension
scoring formula.

Categories: ai-models / ai-products / industry / paper / tip
"""

from __future__ import annotations

import os

from wechat_agent.collectors.aihot import AihotCollector
from wechat_agent.collectors.base import SourceTier
from wechat_agent.collectors.bing_wechat import BingWeChatCollector
from wechat_agent.collectors.github_releases import GitHubReleaseCollector
from wechat_agent.collectors.hacker_news import HackerNewsCollector
from wechat_agent.collectors.http import build_http_client
from wechat_agent.collectors.live import LiveRadarCollector
from wechat_agent.collectors.rss import OfficialFeedCollector
from wechat_agent.domain.models import TopicCategory
from wechat_agent.domain.relevance import AI_RELEVANCE_KEYWORDS


def build_default_live_collector() -> LiveRadarCollector:
    timeout = float(os.getenv("WECHAT_AGENT_HTTP_TIMEOUT_SECONDS", "12"))
    client = build_http_client(timeout)

    # ═══════════════════════════════════════════════════════════════
    # ai-models — 模型发布/更新 (T1 官方一手)
    # ═══════════════════════════════════════════════════════════════
    models_t1 = [
        OfficialFeedCollector(client, name="OpenAI News", url="https://openai.com/news/rss.xml", source_identity="official:openai", category=TopicCategory.AI_MODELS, limit=15, tier=SourceTier.T1),
        OfficialFeedCollector(client, name="Anthropic Blog", url="https://www.anthropic.com/blog/feed.xml", source_identity="official:anthropic", category=TopicCategory.AI_MODELS, limit=15, tier=SourceTier.T1, enabled=False),
        OfficialFeedCollector(client, name="Google AI Blog", url="https://blog.research.google/feeds/posts/default", source_identity="official:google-ai", category=TopicCategory.AI_MODELS, limit=15, tier=SourceTier.T1, enabled=False),
        OfficialFeedCollector(client, name="Meta AI Blog", url="https://ai.meta.com/blog/feed/", source_identity="official:meta-ai", category=TopicCategory.AI_MODELS, limit=15, tier=SourceTier.T1, enabled=False),
        OfficialFeedCollector(client, name="Microsoft AI Blog", url="https://news.microsoft.com/source/topics/ai/feed/", source_identity="official:microsoft-ai", category=TopicCategory.AI_MODELS, limit=10, tier=SourceTier.T1),
        OfficialFeedCollector(client, name="DeepSeek Blog", url="https://api-docs.deepseek.com/news/feed.xml", source_identity="official:deepseek", category=TopicCategory.AI_MODELS, limit=10, tier=SourceTier.T1_5, enabled=False),
        OfficialFeedCollector(client, name="Stability AI Blog", url="https://stability.ai/news/feed", source_identity="official:stability-ai", category=TopicCategory.AI_MODELS, limit=10, tier=SourceTier.T1_5, enabled=False),
        OfficialFeedCollector(client, name="Mistral AI Blog", url="https://mistral.ai/rss.xml", source_identity="official:mistral-ai", category=TopicCategory.AI_MODELS, limit=10, tier=SourceTier.T1_5, enabled=False),
        GitHubReleaseCollector(client, name="Mistral Common Releases", repository="mistralai/mistral-common", source_identity="official:mistral-common", publisher_group="official:mistral-ai", category=TopicCategory.AI_MODELS, limit=10, tier=SourceTier.T1_5),
    ]

    # ═══════════════════════════════════════════════════════════════
    # ai-products — AI 产品发布/更新
    # ═══════════════════════════════════════════════════════════════
    products_t1 = [
        OfficialFeedCollector(client, name="GitHub AI & ML Blog", url="https://github.blog/ai-and-ml/feed/", source_identity="official:github-blog", category=TopicCategory.AI_PRODUCTS, limit=15, tier=SourceTier.T1),
        OfficialFeedCollector(client, name="Hugging Face Blog", url="https://huggingface.co/blog/feed.xml", source_identity="official:hugging-face", category=TopicCategory.AI_PRODUCTS, limit=15, tier=SourceTier.T1, enabled=False),
        GitHubReleaseCollector(client, name="Google Gemini CLI Releases", repository="google-gemini/gemini-cli", source_identity="official:google-gemini-cli", publisher_group="official:google-ai", category=TopicCategory.AI_PRODUCTS, limit=10, tier=SourceTier.T1),
        GitHubReleaseCollector(client, name="Hugging Face Hub Releases", repository="huggingface/huggingface_hub", source_identity="official:huggingface-hub", publisher_group="official:hugging-face", category=TopicCategory.AI_PRODUCTS, limit=10, tier=SourceTier.T1),
        OfficialFeedCollector(client, name="OpenAI Changelog", url="https://help.openai.com/en/collections/10082704-changelog/rss.xml", source_identity="official:openai-changelog", publisher_group="official:openai", category=TopicCategory.AI_PRODUCTS, limit=10, tier=SourceTier.T1_5, enabled=False),
        OfficialFeedCollector(client, name="Vercel AI Blog", url="https://vercel.com/blog/feed.xml", source_identity="official:vercel", category=TopicCategory.AI_PRODUCTS, limit=10, tier=SourceTier.T2),
        OfficialFeedCollector(client, name="Replit Blog", url="https://blog.replit.com/feed", source_identity="official:replit", category=TopicCategory.AI_PRODUCTS, limit=10, tier=SourceTier.T2, enabled=False),
    ]

    # ═══════════════════════════════════════════════════════════════
    # industry — 行业动态
    # ═══════════════════════════════════════════════════════════════
    industry_sources = [
        OfficialFeedCollector(client, name="机器之心", url="https://jiqizhixin.com/rss", source_identity="publisher:jiqizhixin", category=TopicCategory.INDUSTRY, limit=15, tier=SourceTier.T1_5, enabled=False),
        OfficialFeedCollector(client, name="量子位", url="https://www.qbitai.com/feed", source_identity="publisher:qbitai", category=TopicCategory.INDUSTRY, limit=15, tier=SourceTier.T1_5),
        OfficialFeedCollector(client, name="IT之家", url="https://www.ithome.com/rss/", source_identity="publisher:ithome", category=TopicCategory.INDUSTRY, limit=10, tier=SourceTier.T2, relevance_keywords=AI_RELEVANCE_KEYWORDS),
        OfficialFeedCollector(client, name="TechCrunch AI", url="https://techcrunch.com/tag/artificial-intelligence/feed/", source_identity="publisher:techcrunch", category=TopicCategory.INDUSTRY, limit=10, tier=SourceTier.T2),
        OfficialFeedCollector(client, name="The Verge AI", url="https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", source_identity="publisher:the-verge", category=TopicCategory.INDUSTRY, limit=10, tier=SourceTier.T2),
        OfficialFeedCollector(client, name="WIRED AI", url="https://www.wired.com/feed/tag/ai/latest/rss", source_identity="publisher:wired", category=TopicCategory.INDUSTRY, limit=10, tier=SourceTier.T2),
        HackerNewsCollector(client, limit=15, scan_limit=60, minimum_points=20),
    ]

    # ═══════════════════════════════════════════════════════════════
    # paper — 论文研究
    # ═══════════════════════════════════════════════════════════════
    paper_sources = [
        OfficialFeedCollector(client, name="arXiv cs.AI", url="https://rss.arxiv.org/rss/cs.AI", source_identity="academic:arxiv-ai", publisher_group="academic:arxiv", category=TopicCategory.PAPER, limit=20, tier=SourceTier.T1),
        OfficialFeedCollector(client, name="arXiv cs.CL", url="https://rss.arxiv.org/rss/cs.CL", source_identity="academic:arxiv-cl", publisher_group="academic:arxiv", category=TopicCategory.PAPER, limit=15, tier=SourceTier.T1),
        OfficialFeedCollector(client, name="arXiv cs.CV", url="https://rss.arxiv.org/rss/cs.CV", source_identity="academic:arxiv-cv", publisher_group="academic:arxiv", category=TopicCategory.PAPER, limit=10, tier=SourceTier.T1),
        OfficialFeedCollector(client, name="Papers With Code", url="https://paperswithcode.com/feed/latest", source_identity="aggregator:paperswithcode", category=TopicCategory.PAPER, limit=10, tier=SourceTier.T1_5, enabled=False),
        OfficialFeedCollector(client, name="OpenReview Featured", url="https://openreview.net/feed", source_identity="academic:openreview", category=TopicCategory.PAPER, limit=10, tier=SourceTier.T1_5, enabled=False),
    ]

    # ═══════════════════════════════════════════════════════════════
    # tip — 技巧与观点
    # ═══════════════════════════════════════════════════════════════
    tip_sources = [
        OfficialFeedCollector(client, name="Simon Willison", url="https://simonwillison.net/atom/everything/", source_identity="blog:simon-willison", category=TopicCategory.TIP, limit=15, tier=SourceTier.T2),
        OfficialFeedCollector(client, name="少数派", url="https://sspai.com/feed", source_identity="publisher:sspai", category=TopicCategory.TIP, limit=15, tier=SourceTier.T2, relevance_keywords=AI_RELEVANCE_KEYWORDS),
        OfficialFeedCollector(client, name="Towards Data Science", url="https://towardsdatascience.com/feed", source_identity="blog:towards-data-science", category=TopicCategory.TIP, limit=10, tier=SourceTier.T2),
        OfficialFeedCollector(client, name="Lilian Weng Blog", url="https://lilianweng.github.io/index.xml", source_identity="blog:lilian-weng", category=TopicCategory.TIP, limit=10, tier=SourceTier.T2),
        OfficialFeedCollector(client, name="Chip Huyen Blog", url="https://huyenchip.com/feed.xml", source_identity="blog:chip-huyen", category=TopicCategory.TIP, limit=10, tier=SourceTier.T2),
    ]

    # ═══════════════════════════════════════════════════════════════
    # Assemble all collectors
    # ═══════════════════════════════════════════════════════════════
    collectors = [
        *models_t1, *products_t1, *industry_sources,
        *paper_sources, *tip_sources,
    ]

    # Optional, isolated external discovery source. AIHOT candidates still pass
    # through the same local deduplication, pre-screening, scoring and curation
    # pipeline as every other collector. Remove this block and the import above
    # to remove ingestion without touching the conversational Agent extension.
    if os.getenv("WECHAT_AGENT_AIHOT_INGEST_ENABLED", "1").lower() in {
        "1", "true", "yes", "on",
    }:
        try:
            aihot_limit = int(os.getenv("WECHAT_AGENT_AIHOT_INGEST_LIMIT", "100"))
        except ValueError:
            aihot_limit = 100
        aihot_limit = min(100, max(1, aihot_limit))
        aihot_mode = os.getenv("WECHAT_AGENT_AIHOT_INGEST_MODE", "all").lower()
        if aihot_mode not in {"all", "selected"}:
            aihot_mode = "all"
        try:
            aihot_timeout = float(
                os.getenv("WECHAT_AGENT_AIHOT_INGEST_TIMEOUT_SECONDS", "12")
            )
        except ValueError:
            aihot_timeout = 12.0
        collectors.append(
            AihotCollector(
                build_http_client(max(1.0, aihot_timeout)),
                mode=aihot_mode,
                limit=aihot_limit,
            )
        )

    # ═══════════════════════════════════════════════════════════════
    # Optional: Bing WeChat public index (T2)
    # ═══════════════════════════════════════════════════════════════
    if os.getenv("WECHAT_AGENT_ENABLE_WECHAT_INDEX", "1") != "0":
        collectors.extend([
            BingWeChatCollector(client, name="公众号·AI工具", query="site:mp.weixin.qq.com/s 热门 AI 工具 推荐", category=TopicCategory.AI_PRODUCTS, limit=10, tier=SourceTier.T2),
            BingWeChatCollector(client, name="公众号·AI时讯", query="site:mp.weixin.qq.com/s AI 发布 更新 热点", category=TopicCategory.INDUSTRY, limit=10, tier=SourceTier.T2),
            BingWeChatCollector(client, name="公众号·AI模型", query="site:mp.weixin.qq.com/s 人工智能 大模型 热门", category=TopicCategory.AI_MODELS, limit=10, tier=SourceTier.T2),
        ])

    return LiveRadarCollector(collectors)
