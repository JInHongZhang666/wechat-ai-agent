"""Best-effort discovery of public WeChat articles through Bing RSS."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

from wechat_agent.collectors.base import (
    CollectionResult,
    SourceTier,
    failed_collection_result,
)
from wechat_agent.collectors.http import HttpGetter
from wechat_agent.collectors.utils import parse_datetime, plain_text, stable_id
from wechat_agent.domain.models import (
    ArticleRecord,
    SourceKind,
    SourceRunStatus,
    SourceType,
    TopicCategory,
)


class BingWeChatCollector:
    """Discover indexed public pages without login, CAPTCHA, or anti-bot bypass."""

    _endpoint = "https://www.bing.com/search"

    def __init__(
        self,
        client: HttpGetter,
        *,
        name: str,
        query: str,
        category: TopicCategory,
        limit: int = 10,
        tier: SourceTier = SourceTier.T2,
        enabled: bool = True,
    ) -> None:
        self.client = client
        self.name = name
        self.query = query
        self.category = category
        self.source_id = f"wechat-index:{category.value}"
        self.categories = frozenset({category})
        self.limit = limit
        self.tier = tier
        self.feed_url = f"{self._endpoint}?format=rss"
        self.homepage = "https://mp.weixin.qq.com"
        self.kind = SourceKind.MP_HOT
        self.publisher_group = self.source_id
        self.evidence_eligible = False
        self.signal_only = True
        self.language = "zh-CN"
        self.schedule_minutes = 30
        self.parser_version = "bing-wechat-v1"
        self.enabled = enabled

    def collect(self, since: datetime, now: datetime) -> CollectionResult:
        try:
            response = self.client.get(
                self._endpoint,
                params={"format": "rss", "q": self.query},
            )
            response.raise_for_status()
            root = ET.fromstring(response.text)
        except Exception as error:
            return failed_collection_result(self.name, error)

        articles: list[ArticleRecord] = []
        skipped_without_date = 0
        items = list(root.iter("item"))
        for item in items:
            link = _text(item, "link")
            title = plain_text(_text(item, "title"), limit=240)
            published_at = parse_datetime(_text(item, "pubDate"))
            if (
                not link
                or not title
                or not _is_public_wechat_article(link)
            ):
                continue
            if published_at is None:
                skipped_without_date += 1
                continue
            if not since <= published_at <= now:
                continue
            description = plain_text(_text(item, "description"))
            articles.append(
                ArticleRecord(
                    id=stable_id("wechat-index", link),
                    source_id=self.source_id,
                    title=title,
                    source_name="微信公众号公开文章",
                    source_url=link,
                    published_at=published_at,
                    collected_at=now,
                    summary=plain_text(
                        "Public WeChat search index includes this article. "
                        f"{description or title}"
                    ),
                    category=self.category,
                    source_confidence=0.45,
                    source_type=SourceType.SECONDARY,
                    source_identity=f"wechat-index:{urlsplit(link).hostname}",
                    source_type_verified=False,
                    source_verification_basis=(
                        "Bing 公开 RSS 搜索索引发现；日期来自搜索索引，"
                        "不代表公众号后台发布时间，且不提供真实阅读量或点赞量。"
                    ),
                )
            )
            if len(articles) >= self.limit:
                break
        warnings = (
            (f"{self.name}跳过 {skipped_without_date} 条无可核验日期的结果。",)
            if skipped_without_date
            else ()
        )
        return CollectionResult(
            articles=tuple(articles),
            warnings=warnings,
            status=(
                SourceRunStatus.PARTIAL
                if warnings
                else SourceRunStatus.SUCCESS
            ),
            http_status=getattr(response, "status_code", 200),
            fetched_count=len(items),
            skipped_count=max(0, len(items) - len(articles)),
        )


def _text(item: ET.Element, name: str) -> str | None:
    child = item.find(name)
    return (child.text or "").strip() if child is not None else None


def _is_public_wechat_article(url: str) -> bool:
    parts = urlsplit(url)
    return (
        parts.scheme in {"http", "https"}
        and (parts.hostname or "").casefold() == "mp.weixin.qq.com"
        and parts.path.startswith("/s")
    )
