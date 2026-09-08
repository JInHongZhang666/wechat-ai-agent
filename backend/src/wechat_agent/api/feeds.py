"""RSS Feed endpoints — AIHOT-compatible multi-format output.

Provides four RSS 2.0 feeds:
  /feed.xml              → Curated selected items (latest 50)
  /feed/all.xml          → All public items (7-day window)
  /feed/daily.xml        → Daily reports (latest 30)
  /feed/category/{slug}.xml → Category-filtered curated items
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.dom import minidom

from fastapi import APIRouter, Response

from wechat_agent.api.routes import ContentApiService
from wechat_agent.api.v1_routes import _get_item_rows
from wechat_agent.domain.models import public_category_slug
from wechat_agent.services.daily import (
    daily_report_payload,
    ensure_recent_daily_reports,
    list_daily_reports,
)

ServiceProvider = Callable[[], ContentApiService]

# Category slug mapping
_SLUG_MAP = {
    "ai-models": "ai-models",
    "ai-products": "ai-products",
    "industry": "industry",
    "paper": "paper",
    "tip": "tip",
}
_SLUG_LABELS = {
    "ai-models": "AI 模型",
    "ai-products": "AI 产品",
    "industry": "行业动态",
    "paper": "论文",
    "tip": "技巧观点",
}


def create_feed_router(service_provider: ServiceProvider) -> APIRouter:
    """Create router for RSS feed endpoints."""
    router = APIRouter()

    @router.get("/feed.xml", include_in_schema=False)
    def curated_feed() -> Response:
        """精选摘要 RSS — latest 50 curated items."""
        service = service_provider()
        articles = _get_all_articles(service, mode="selected")
        return _build_rss_response(
            title="AI 信息小屋 — 精选",
            description="最新 50 条 AI 信息小屋精选摘要，保留标题、站内阅读与原文入口。",
            link="http://127.0.0.1:8010/",
            articles=articles[:50],
            language="zh-CN",
            ttl=30,
        )

    @router.get("/feed/all.xml", include_in_schema=False)
    def all_feed() -> Response:
        """全部动态 RSS — 7-day window."""
        service = service_provider()
        articles = _get_all_articles(service, mode="all")
        cutoff = datetime.now(UTC) - timedelta(days=7)
        recent = [a for a in articles if a.published_at >= cutoff]
        return _build_rss_response(
            title="AI 信息小屋 — 全部 AI 动态",
            description="最近 7 天公开 AI 动态，按真实发布时间倒序。",
            link="http://127.0.0.1:8010/all",
            articles=recent[:100],
            language="zh-CN",
            ttl=15,
        )

    @router.get("/feed/daily.xml", include_in_schema=False)
    def daily_feed() -> Response:
        """日报 RSS — latest 30 daily reports."""
        service = service_provider()
        return _build_daily_rss_response(service)

    @router.get("/feed/category/{slug}.xml", include_in_schema=False)
    def category_feed(slug: str) -> Response:
        """分类 RSS — curated items by category."""
        if slug not in _SLUG_MAP:
            from fastapi.responses import Response as FastResponse
            return FastResponse(status_code=404, content="Unknown category slug")
        service = service_provider()
        articles = _get_all_articles(service, mode="selected")
        mapped_cat = _SLUG_MAP[slug]
        filtered = [
            article
            for article in articles
            if public_category_slug(article.category) == mapped_cat
        ]
        label = _SLUG_LABELS.get(slug, slug)
        return _build_rss_response(
            title=f"AI 信息小屋 — {label}",
            description=f"AI 信息小屋 {label} 精选动态。",
            link=f"http://127.0.0.1:8010/category/{slug}",
            articles=filtered[:50],
            language="zh-CN",
            ttl=30,
        )

    return router


def _get_all_articles(service: ContentApiService, mode: str = "selected"):
    """Read the same persisted assessments used by the JSON API."""
    rows = _get_item_rows(service)
    if mode == "selected":
        rows = [row for row in rows if row.decision.selected]
    return [row.article for row in rows]


def _build_rss_response(
    *,
    title: str,
    description: str,
    link: str,
    articles: list,
    language: str = "zh-CN",
    ttl: int = 30,
) -> Response:
    """Build an RSS 2.0 XML response."""
    rss = Element("rss", version="2.0")
    rss.set("xmlns:atom", "http://www.w3.org/2005/Atom")
    rss.set("xmlns:content", "http://purl.org/rss/1.0/modules/content/")

    channel = SubElement(rss, "channel")
    SubElement(channel, "title").text = title
    SubElement(channel, "link").text = link
    SubElement(channel, "description").text = description
    SubElement(channel, "language").text = language
    SubElement(channel, "ttl").text = str(ttl)
    SubElement(channel, "generator").text = "AI 信息小屋 (https://github.com/ai-cabin)"

    # Atom self link
    atom_link = SubElement(channel, "{http://www.w3.org/2005/Atom}link")
    atom_link.set("href", link)
    atom_link.set("rel", "self")
    atom_link.set("type", "application/rss+xml")

    for article in articles:
        item = SubElement(channel, "item")
        title_el = SubElement(item, "title")
        title_el.text = article.title
        SubElement(item, "link").text = article.source_url
        desc_el = SubElement(item, "description")
        desc_el.text = (
            f"<p>{article.summary}</p>"
            f"<p>🔗 <a href=\"{article.source_url}\">阅读原文</a></p>"
            f"<p>via AI 信息小屋 · <a href=\"http://127.0.0.1:8010/\">"
            f"http://127.0.0.1:8010/</a></p>"
        )
        SubElement(item, "category").text = public_category_slug(article.category)
        SubElement(item, "pubDate").text = format_datetime(
            article.published_at.astimezone(UTC),
            usegmt=True,
        )
        guid = SubElement(item, "guid")
        guid.set("isPermaLink", "false")
        guid.text = article.id
        SubElement(item, "author").text = (
            f"noreply@ai-cabin.local ({article.source_name})"
        )

    # Pretty-print XML
    xml_str = minidom.parseString(
        tostring(rss, encoding="unicode")
    ).toprettyxml(indent="  ")

    return Response(
        content=xml_str,
        media_type="application/rss+xml; charset=utf-8",
    )


def _build_daily_rss_response(service: ContentApiService) -> Response:
    """Build RSS from the same immutable report revisions used by JSON."""
    reports = list_daily_reports(service, limit=30)
    if not reports:
        reports = ensure_recent_daily_reports(service, limit=30)
    rss = Element("rss", version="2.0")
    channel = SubElement(rss, "channel")
    SubElement(channel, "title").text = "AI 信息小屋 · 日报"
    SubElement(channel, "link").text = "http://127.0.0.1:8010/daily"
    SubElement(channel, "description").text = "按日期汇总的 AI 信息小屋精编日报。"
    SubElement(channel, "language").text = "zh-CN"
    SubElement(channel, "ttl").text = "60"

    for stored_report in reports:
        report = daily_report_payload(stored_report)
        report_date = stored_report.date
        item = SubElement(channel, "item")
        revision_suffix = (
            f"（修订 {stored_report.revision}）"
            if stored_report.revision > 1
            else ""
        )
        SubElement(item, "title").text = (
            f"{report_date.isoformat()} AI 日报{revision_suffix}"
        )
        link = f"http://127.0.0.1:8010/daily/{report_date.isoformat()}"
        SubElement(item, "link").text = link
        section_lines = [
            f"<li>{section['label']}：{section['count']} 条</li>"
            for section in report.get("sections", [])
        ]
        SubElement(item, "description").text = (
            f"<p>{report.get('lead', '')}</p><ul>{''.join(section_lines)}</ul>"
        )
        published = (
            stored_report.published_at or stored_report.generated_at
        ).astimezone(UTC)
        SubElement(item, "pubDate").text = format_datetime(published, usegmt=True)
        guid = SubElement(item, "guid")
        guid.set("isPermaLink", "false")
        guid.text = f"daily:{report_date.isoformat()}:r{stored_report.revision}"

    xml_str = minidom.parseString(
        tostring(rss, encoding="unicode")
    ).toprettyxml(indent="  ")
    return Response(
        content=xml_str,
        media_type="application/rss+xml; charset=utf-8",
    )
