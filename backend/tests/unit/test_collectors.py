from datetime import UTC, datetime

import httpx

from wechat_agent.collectors.base import (
    CollectionResult,
    failed_collection_result,
)
from wechat_agent.collectors.bing_wechat import BingWeChatCollector
from wechat_agent.collectors.defaults import build_default_live_collector
from wechat_agent.collectors.github_releases import GitHubReleaseCollector
from wechat_agent.collectors.hacker_news import HackerNewsCollector
from wechat_agent.collectors.http import RetryingHttpGetter
from wechat_agent.collectors.live import LiveRadarCollector
from wechat_agent.collectors.registry import sources_from_live_collector
from wechat_agent.collectors.rss import OfficialFeedCollector
from wechat_agent.domain.models import (
    ArticleRecord,
    SourceRunStatus,
    SourceType,
    SourceKind,
    TopicCategory,
)


NOW = datetime(2026, 7, 26, 8, tzinfo=UTC)
SINCE = datetime(2026, 7, 19, 8, tzinfo=UTC)


class StubResponse:
    def __init__(
        self,
        *,
        text: str = "",
        payload: object = None,
        status_code: int = 200,
    ) -> None:
        self.text = text
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload

    def raise_for_status(self) -> None:
        return None


class StubClient:
    def __init__(self, response: StubResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, dict | None, dict | None]] = []

    def get(self, url, *, params=None, headers=None):
        self.calls.append((url, params, headers))
        return self.response


class RoutingStubClient:
    def __init__(self, responses: dict[str, StubResponse | Exception]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url, *, params=None, headers=None):
        self.calls.append(url)
        result = self.responses[url]
        if isinstance(result, Exception):
            raise result
        return result


def test_official_feed_collector_supports_custom_category():
    """RSS collector should respect the category passed at construction."""
    client = StubClient(
        StubResponse(
            text="""<?xml version="1.0"?>
            <rss><channel>
              <item>
                <title>热门开源工具盘点</title>
                <link>https://sspai.com/post/12345</link>
                <pubDate>Fri, 24 Jul 2026 09:00:00 GMT</pubDate>
                <description>本周值得关注的 AI 工具。</description>
              </item>
            </channel></rss>"""
        )
    )

    result = OfficialFeedCollector(
        client,
        name="少数派",
        url="https://sspai.com/feed",
        source_identity="publisher:sspai",
        category=TopicCategory.TOOL,
    ).collect(SINCE, NOW)

    assert len(result.articles) == 1
    article = result.articles[0]
    assert article.category is TopicCategory.TOOL
    assert article.source_type is SourceType.OFFICIAL
    assert article.source_id == "publisher:sspai"
    assert article.source_url == "https://sspai.com/post/12345"


def test_github_release_collector_keeps_only_stable_in_window_releases():
    client = StubClient(
        StubResponse(
            payload=[
                {
                    "name": "Release v1.2.0",
                    "tag_name": "v1.2.0",
                    "html_url": "https://github.com/example/project/releases/tag/v1.2.0",
                    "published_at": "2026-07-24T09:00:00Z",
                    "body": "Stable release with a production-ready feature.",
                    "draft": False,
                    "prerelease": False,
                },
                {
                    "name": "Release v1.3.0-nightly.20260725",
                    "tag_name": "v1.3.0-nightly.20260725",
                    "html_url": "https://github.com/example/project/releases/tag/nightly",
                    "published_at": "2026-07-25T09:00:00Z",
                    "body": "Nightly build.",
                    "draft": False,
                    "prerelease": False,
                },
                {
                    "name": "Release v1.3.0-rc1",
                    "tag_name": "v1.3.0-rc1",
                    "html_url": "https://github.com/example/project/releases/tag/rc1",
                    "published_at": "2026-07-25T10:00:00Z",
                    "body": "Release candidate.",
                    "draft": False,
                    "prerelease": True,
                },
                {
                    "name": "Release v1.1.0",
                    "tag_name": "v1.1.0",
                    "html_url": "https://github.com/example/project/releases/tag/v1.1.0",
                    "published_at": "2026-07-01T09:00:00Z",
                    "body": "Outside the collection window.",
                    "draft": False,
                    "prerelease": False,
                },
            ]
        )
    )

    result = GitHubReleaseCollector(
        client,
        name="Example Releases",
        repository="example/project",
        source_identity="official:example-releases",
        publisher_group="official:example",
        category=TopicCategory.TOOL,
    ).collect(SINCE, NOW)

    assert result.http_status == 200
    assert result.fetched_count == 4
    assert result.skipped_count == 3
    assert [article.title for article in result.articles] == ["Release v1.2.0"]
    article = result.articles[0]
    assert article.source_id == "official:example-releases"
    assert article.source_type is SourceType.OFFICIAL
    assert article.category is TopicCategory.TOOL
    assert "example/project official GitHub Releases API" in (
        article.source_verification_basis or ""
    )
    assert client.calls == [
        (
            "https://api.github.com/repos/example/project/releases?per_page=30",
            None,
            {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
    ]


def test_github_release_collector_uses_optional_token(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    client = StubClient(StubResponse(payload=[]))

    result = GitHubReleaseCollector(
        client,
        name="Example Releases",
        repository="example/project",
        source_identity="official:example-releases",
        category=TopicCategory.TOOL,
    ).collect(SINCE, NOW)

    assert result.status is SourceRunStatus.SUCCESS
    assert client.calls[0][2]["Authorization"] == "Bearer test-token"


def test_official_feed_collector_supports_rss_and_filters_window():
    client = StubClient(
        StubResponse(
            text="""<?xml version="1.0"?>
            <rss><channel>
              <item>
                <title>New model update</title>
                <link>https://example.com/news/new-model</link>
                <pubDate>Fri, 24 Jul 2026 09:00:00 GMT</pubDate>
                <description><![CDATA[<p>Official release details.</p>]]></description>
              </item>
              <item>
                <title>Old update</title>
                <link>https://example.com/news/old</link>
                <pubDate>Wed, 01 Jul 2026 09:00:00 GMT</pubDate>
              </item>
            </channel></rss>"""
        )
    )

    result = OfficialFeedCollector(
        client,
        name="Example AI",
        url="https://example.com/feed.xml",
        source_identity="official:example",
    ).collect(SINCE, NOW)

    assert [article.title for article in result.articles] == ["New model update"]
    article = result.articles[0]
    assert article.category is TopicCategory.NEWS
    assert article.source_type is SourceType.OFFICIAL
    assert article.source_id == "official:example"
    assert article.source_type_verified is True
    assert "<p>" not in article.summary
    assert result.status is SourceRunStatus.SUCCESS
    assert result.http_status == 200
    assert result.fetched_count == 2
    assert result.skipped_count == 1


def test_official_feed_collector_returns_stable_parse_diagnostic():
    result = OfficialFeedCollector(
        StubClient(StubResponse(text="<rss>")),
        name="Broken XML",
        url="https://example.com/feed.xml",
        source_identity="official:broken",
    ).collect(SINCE, NOW)

    assert result.status is SourceRunStatus.FAILED
    assert result.error_code == "parse_error"
    assert result.error_summary == "ParseError"
    assert result.stage == "parse"
    assert result.fetched_count == 0


def test_http_429_diagnostic_preserves_retry_after_without_response_details():
    request = httpx.Request("GET", "https://example.com/feed.xml")
    response = httpx.Response(
        429,
        request=request,
        headers={"Retry-After": "120"},
    )
    error = httpx.HTTPStatusError(
        "rate limited with sensitive upstream details",
        request=request,
        response=response,
    )

    result = failed_collection_result("Rate Limited Feed", error)

    assert result.status is SourceRunStatus.FAILED
    assert result.http_status == 429
    assert result.error_code == "http_429"
    assert result.error_summary == "HTTPStatusError"
    assert result.stage == "fetch"
    assert result.retry_after_seconds == 120
    assert "sensitive" not in result.warnings[0]


def test_general_feed_can_filter_non_ai_items_with_relevance_keywords():
    client = StubClient(
        StubResponse(
            text="""<?xml version="1.0"?>
            <rss><channel>
              <item>
                <title>普通游戏联动公告</title>
                <link>https://example.com/game</link>
                <pubDate>Fri, 24 Jul 2026 09:00:00 GMT</pubDate>
                <description>普通游戏资讯与角色更新。</description>
              </item>
              <item>
                <title>Gemini 智能体功能上线</title>
                <link>https://example.com/gemini-agent</link>
                <pubDate>Fri, 24 Jul 2026 08:00:00 GMT</pubDate>
                <description>新的 AI Agent 可以处理任务。</description>
              </item>
            </channel></rss>"""
        )
    )

    result = OfficialFeedCollector(
        client,
        name="综合科技媒体",
        url="https://example.com/feed.xml",
        source_identity="publisher:example",
        relevance_keywords=("ai", "智能体", "gemini"),
    ).collect(SINCE, NOW)

    assert [article.title for article in result.articles] == ["Gemini 智能体功能上线"]


def test_hacker_news_collector_uses_official_api_and_filters_ai_stories():
    published_at = int(datetime(2026, 7, 24, 9, tzinfo=UTC).timestamp())
    top_url = "https://hacker-news.firebaseio.com/v0/topstories.json"
    item_url = "https://hacker-news.firebaseio.com/v0/item/{item_id}.json"
    client = RoutingStubClient(
        {
            top_url: StubResponse(payload=[101, 102]),
            item_url.format(item_id=101): StubResponse(
                payload={
                    "id": 101,
                    "type": "story",
                    "title": "New open source language model ships",
                    "url": "https://research.example.ai/model",
                    "time": published_at,
                    "score": 125,
                    "descendants": 34,
                }
            ),
            item_url.format(item_id=102): StubResponse(
                payload={
                    "id": 102,
                    "type": "story",
                    "title": "A new database storage engine",
                    "url": "https://database.example.dev/release",
                    "time": published_at,
                    "score": 80,
                    "descendants": 12,
                }
            ),
        }
    )

    result = HackerNewsCollector(client, limit=5, scan_limit=5).collect(SINCE, NOW)

    assert result.status is SourceRunStatus.SUCCESS
    assert result.fetched_count == 2
    assert [article.id for article in result.articles] == ["hn-101"]
    article = result.articles[0]
    assert article.source_id == "aggregator:hacker-news"
    assert article.source_url == "https://research.example.ai/model"
    assert article.engagement is not None
    assert article.engagement.comments == 34
    assert article.engagement.verified_proxy_signals == ("comments",)


def test_retrying_http_getter_retries_transport_and_5xx_failures():
    request = httpx.Request("GET", "https://feed.example.test")

    class SequenceClient:
        def __init__(self):
            self.results = [
                httpx.RemoteProtocolError("disconnect", request=request),
                StubResponse(status_code=502),
                StubResponse(status_code=200),
            ]

        def get(self, url, *, params=None, headers=None):
            result = self.results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

    delays: list[float] = []
    client = SequenceClient()
    response = RetryingHttpGetter(
        client,
        max_attempts=3,
        base_delay_seconds=0.1,
        sleep_fn=delays.append,
    ).get("https://feed.example.test")

    assert response.status_code == 200
    assert delays == [0.1, 0.2]


def test_default_hacker_news_source_no_longer_depends_on_hnrss(monkeypatch):
    monkeypatch.setenv("WECHAT_AGENT_ENABLE_WECHAT_INDEX", "0")

    collector = build_default_live_collector()
    hacker_news = next(
        item
        for item in collector.collectors
        if item.source_id == "aggregator:hacker-news"
    )

    assert isinstance(hacker_news, HackerNewsCollector)
    assert hacker_news.feed_url == (
        "https://hacker-news.firebaseio.com/v0/topstories.json"
    )


def test_default_replacement_sources_are_official_stable_release_collectors(
    monkeypatch,
):
    monkeypatch.setenv("WECHAT_AGENT_ENABLE_WECHAT_INDEX", "0")

    collector = build_default_live_collector()
    by_id = {item.source_id: item for item in collector.collectors}

    assert by_id["official:google-ai"].enabled is False
    assert by_id["official:hugging-face"].enabled is False
    assert by_id["official:mistral-ai"].enabled is False
    replacements = {
        "official:google-gemini-cli": "official:google-ai",
        "official:huggingface-hub": "official:hugging-face",
        "official:mistral-common": "official:mistral-ai",
    }
    for source_id, publisher_group in replacements.items():
        replacement = by_id[source_id]
        assert isinstance(replacement, GitHubReleaseCollector)
        assert replacement.enabled is True
        assert replacement.publisher_group == publisher_group
        assert replacement.parser_version == "github-stable-releases-v1"


def test_wechat_index_accepts_only_direct_public_article_urls_with_dates():
    client = StubClient(
        StubResponse(
            text="""<?xml version="1.0"?>
            <rss><channel>
              <item>
                <title>AI 工具教程</title>
                <link>https://mp.weixin.qq.com/s/abc123</link>
                <pubDate>Fri, 24 Jul 2026 09:00:00 GMT</pubDate>
                <description>公开索引摘要</description>
              </item>
              <item>
                <title>非公众号结果</title>
                <link>https://example.com/not-wechat</link>
                <pubDate>Fri, 24 Jul 2026 09:00:00 GMT</pubDate>
              </item>
              <item>
                <title>无日期公众号结果</title>
                <link>https://mp.weixin.qq.com/s/no-date</link>
              </item>
            </channel></rss>"""
        )
    )

    result = BingWeChatCollector(
        client,
        name="公众号索引测试",
        query="site:mp.weixin.qq.com/s AI",
        category=TopicCategory.TOOL,
    ).collect(SINCE, NOW)

    assert [article.source_url for article in result.articles] == [
        "https://mp.weixin.qq.com/s/abc123"
    ]
    article = result.articles[0]
    assert article.source_confidence == 0.45
    assert article.source_type_verified is False
    assert article.engagement is None
    assert "不提供真实阅读量或点赞量" in article.source_verification_basis
    assert "跳过 1 条" in result.warnings[0]
    assert article.source_id == "wechat-index:tool"


def test_collector_configuration_becomes_non_destructive_source_seed():
    collector = OfficialFeedCollector(
        StubClient(StubResponse(text="<rss><channel /></rss>")),
        name="Example AI",
        url="https://example.com/feed.xml",
        source_identity="official:example",
        category=TopicCategory.AI_MODELS,
    )

    sources = sources_from_live_collector(
        LiveRadarCollector([collector]),
        now=NOW,
    )

    assert len(sources) == 1
    source = sources[0]
    assert source.id == "official:example"
    assert source.kind is SourceKind.RSS
    assert source.categories == (TopicCategory.AI_MODELS,)
    assert source.evidence_eligible is True
    assert source.signal_only is False
    assert source.created_at == NOW


class WindowCollector:
    def __init__(self, name, category, seven_day_count, thirty_day_count):
        self.name = name
        self.categories = frozenset({category})
        self.category = category
        self.seven_day_count = seven_day_count
        self.thirty_day_count = thirty_day_count
        self.windows: list[int] = []
        self.source_id = f"test:{name}"
        self.enabled = True

    def collect(self, since, now):
        days = (now - since).days
        self.windows.append(days)
        count = self.seven_day_count if days == 7 else self.thirty_day_count
        return CollectionResult(
            articles=tuple(
                _article(f"{self.name}-{index}", self.category, now)
                for index in range(count)
            )
        )


def test_live_radar_expands_only_short_category_to_thirty_days():
    tools = WindowCollector("tools", TopicCategory.TOOL, 2, 5)
    news = WindowCollector("news", TopicCategory.NEWS, 5, 8)

    result = LiveRadarCollector([tools, news]).collect(NOW)

    assert result.window_days == 30
    assert result.expanded_categories == (TopicCategory.TOOL,)
    assert tools.windows == [7, 30]
    assert news.windows == [7]
    assert [(run.source_id, run.window_days) for run in result.source_runs] == [
        ("test:news", 7),
        ("test:tools", 7),
        ("test:tools", 30),
    ]
    assert all(run.status is SourceRunStatus.SUCCESS for run in result.source_runs)
    assert sum(
        article.category is TopicCategory.TOOL for article in result.articles
    ) == 5
    assert sum(
        article.category is TopicCategory.NEWS for article in result.articles
    ) == 5


def test_live_radar_skips_disabled_collectors():
    disabled = WindowCollector("disabled", TopicCategory.TOOL, 5, 5)
    disabled.enabled = False

    result = LiveRadarCollector([disabled]).collect(NOW)

    assert disabled.windows == []
    assert result.articles == ()
    assert result.source_runs == ()


def test_claimed_source_collection_can_disable_thirty_day_expansion():
    collector = WindowCollector("due", TopicCategory.TOOL, 1, 5)

    result = LiveRadarCollector([collector]).collect_sources(
        NOW,
        {collector.source_id},
        expand_shortages=False,
    )

    assert collector.windows == [7]
    assert result.window_days == 7
    assert result.expanded_categories == ()
    assert [run.window_days for run in result.source_runs] == [7]


def _article(
    identity: str,
    category: TopicCategory,
    now: datetime,
) -> ArticleRecord:
    return ArticleRecord(
        id=identity,
        title=identity,
        source_name=identity,
        source_url=f"https://example.com/{identity}",
        published_at=now,
        collected_at=now,
        summary=f"{identity} has 1 source.",
        category=category,
    )
