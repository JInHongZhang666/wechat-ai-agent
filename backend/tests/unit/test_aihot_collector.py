"""AIHOT ingestion is optional, isolated and locally re-evaluated."""

from datetime import UTC, datetime, timedelta

from wechat_agent.collectors.aihot import (
    AIHOT_ITEMS_URL,
    AIHOT_USER_AGENT,
    AihotCollector,
)
from wechat_agent.collectors.base import merge_by_url
from wechat_agent.collectors.defaults import build_default_live_collector
from wechat_agent.domain.models import (
    ArticleRecord,
    SourceKind,
    SourceRunStatus,
    SourceType,
    TopicCategory,
)


NOW = datetime(2026, 10, 27, 2, tzinfo=UTC)
SINCE = NOW - timedelta(days=7)


class Response:
    def __init__(self, payload=None, *, status_code=200, headers=None):
        self.payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class Getter:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, *, params=None, headers=None):
        self.calls.append((url, params, headers))
        return self.responses.pop(0)


def _item(**updates):
    value = {
        "id": "item-1",
        "title": "Claude 发布新模型",
        "summary": "模型能力与产品能力均有更新。",
        "source": {"name": "Anthropic"},
        "links": {
            "aihot": "https://aihot.news/read/item-1",
            "original": "https://www.anthropic.com/news/model",
        },
        "publishedAt": "2026-10-26T01:00:00Z",
        "discoveredAt": "2026-10-26T02:00:00Z",
        "category": "ai-models",
        "score": 91,
        "selected": True,
        "reason": "值得关注",
    }
    value.update(updates)
    return value


def test_aihot_collector_maps_items_for_local_scoring():
    getter = Getter(
        Response(
            {"schemaVersion": 1, "items": [_item()], "page": {}},
            headers={"ETag": '"v1"'},
        )
    )
    collector = AihotCollector(getter, mode="all", limit=80)

    result = collector.collect(SINCE, NOW)

    assert result.status is SourceRunStatus.SUCCESS
    assert result.fetched_count == 1
    assert len(result.articles) == 1
    article = result.articles[0]
    assert article.source_id == "aggregator:aihot"
    assert article.source_name == "Anthropic"
    assert article.source_url == "https://www.anthropic.com/news/model"
    assert article.category is TopicCategory.AI_MODELS
    assert article.tier == "t2"
    assert article.source_type is SourceType.SECONDARY
    assert article.source_type_verified is False
    assert article.source_identity == "external:www.anthropic.com"
    url, params, headers = getter.calls[0]
    assert url == AIHOT_ITEMS_URL
    assert params == {
        "mode": "all", "window": "7d", "by": "timeline", "limit": 80,
    }
    assert headers["User-Agent"] == AIHOT_USER_AGENT
    assert "If-None-Match" not in headers


def test_aihot_collector_uses_etag_and_accepts_304():
    getter = Getter(
        Response({"items": []}, headers={"ETag": '"stable"'}),
        Response(status_code=304),
    )
    collector = AihotCollector(getter)

    first = collector.collect(SINCE, NOW)
    second = collector.collect(SINCE, NOW)

    assert first.status is SourceRunStatus.SUCCESS
    assert second.status is SourceRunStatus.SUCCESS
    assert second.http_status == 304
    assert second.articles == ()
    assert getter.calls[1][2]["If-None-Match"] == '"stable"'


def test_aihot_collector_keeps_unknown_categories_for_local_classification():
    getter = Getter(
        Response({"items": [_item(), _item(id="item-2", category="future-category")]})
    )

    result = AihotCollector(getter).collect(SINCE, NOW)

    assert result.status is SourceRunStatus.SUCCESS
    assert len(result.articles) == 2
    assert result.articles[1].category is TopicCategory.INDUSTRY
    assert result.skipped_count == 0


def test_aihot_collector_reports_malformed_items_without_breaking_valid_items():
    getter = Getter(Response({"items": [_item(), {"id": "broken"}]}))

    result = AihotCollector(getter).collect(SINCE, NOW)

    assert result.status is SourceRunStatus.PARTIAL
    assert len(result.articles) == 1
    assert result.skipped_count == 1
    assert result.error_code == "item_parse_partial"


def test_direct_collector_wins_url_deduplication_over_aihot():
    shared = {
        "id": "direct",
        "title": "Direct version",
        "source_name": "Official",
        "source_url": "https://example.com/story",
        "published_at": NOW,
        "collected_at": NOW - timedelta(minutes=5),
        "summary": "Direct summary",
        "category": TopicCategory.AI_MODELS,
    }
    direct = ArticleRecord(**shared, source_id="official:example")
    aihot = ArticleRecord(
        **{**shared, "id": "aihot", "title": "AIHOT version", "collected_at": NOW},
        source_id="aggregator:aihot",
    )

    merged = merge_by_url(((aihot,), (direct,)))

    assert len(merged) == 1
    assert merged[0].source_id == "official:example"
    assert merged[0].title == "Direct version"


def test_aihot_ingestion_has_an_independent_feature_flag(monkeypatch):
    monkeypatch.setenv("WECHAT_AGENT_ENABLE_WECHAT_INDEX", "0")
    monkeypatch.setenv("WECHAT_AGENT_AIHOT_INGEST_ENABLED", "0")
    without_aihot = build_default_live_collector()
    assert "aggregator:aihot" not in {
        item.source_id for item in without_aihot.collectors
    }

    monkeypatch.setenv("WECHAT_AGENT_AIHOT_INGEST_ENABLED", "1")
    with_aihot = build_default_live_collector()
    aihot = next(
        item for item in with_aihot.collectors
        if item.source_id == "aggregator:aihot"
    )
    assert isinstance(aihot, AihotCollector)
    assert aihot.kind is SourceKind.API
    assert aihot.evidence_eligible is False
    assert aihot.signal_only is True

