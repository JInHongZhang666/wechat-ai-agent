import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient

from wechat_agent.collectors.base import LiveCollectionResult
from wechat_agent.collectors.defaults import build_default_live_collector
from wechat_agent.domain.models import (
    ArticleRecord,
    SourceKind,
    SourceRun,
    SourceRunStatus,
    SourceTier,
    TopicCategory,
)
from wechat_agent.main import create_app


FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "acceptance_articles.json"


class FixtureLiveCollector:
    def __init__(self) -> None:
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        self.articles = tuple(
            ArticleRecord.model_validate(item).model_copy(
                update={"source_id": "fixture:live"}
            )
            for item in payload
        )
        self.collectors = [FixtureSourceConfig()]

    def collect(self, now):
        return LiveCollectionResult(
            articles=self.articles,
            warnings=("公众号索引无可见后台阅读量。",),
            window_days=30,
            expanded_categories=(TopicCategory.NEWS,),
            source_counts={"fixture-live": len(self.articles)},
            source_runs=(
                SourceRun(
                    id=f"fixture-run-{now.timestamp()}",
                    source_id="fixture:live",
                    window_days=30,
                    started_at=now - timedelta(seconds=1),
                    finished_at=now,
                    status=SourceRunStatus.SUCCESS,
                    http_status=200,
                    fetched_count=len(self.articles),
                    duration_ms=1000,
                ),
            ),
        )


class FixtureSourceConfig:
    name = "Fixture Live"
    source_id = "fixture:live"
    source_identity = source_id
    categories = frozenset({TopicCategory.TOOL, TopicCategory.NEWS})
    tier = SourceTier.T2
    kind = SourceKind.RSS
    feed_url = "https://example.com/feed.xml"
    homepage = "https://example.com"
    publisher_group = source_id
    evidence_eligible = True
    signal_only = False
    language = "en"
    schedule_minutes = 30
    parser_version = "fixture-v1"
    enabled = True


class FailingFixtureLiveCollector(FixtureLiveCollector):
    def collect(self, now):
        return LiveCollectionResult(
            warnings=("Fixture Live采集失败：ReadTimeout",),
            window_days=7,
            source_runs=(
                SourceRun(
                    id=f"failed-run-{now.timestamp()}",
                    source_id="fixture:live",
                    window_days=7,
                    started_at=now - timedelta(seconds=1),
                    finished_at=now,
                    status=SourceRunStatus.FAILED,
                    stage="fetch",
                    fetched_count=0,
                    error_code="http_timeout",
                    error_summary="ReadTimeout",
                    duration_ms=1000,
                ),
            ),
        )


class HttpFailureFixtureLiveCollector(FixtureLiveCollector):
    def __init__(self, status_code: int, retry_after_seconds: int | None = None):
        super().__init__()
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds

    def collect(self, now):
        return LiveCollectionResult(
            warnings=(f"Fixture Live采集失败：HTTP {self.status_code}",),
            window_days=7,
            source_runs=(
                SourceRun(
                    id=f"http-run-{self.status_code}-{now.timestamp()}",
                    source_id="fixture:live",
                    window_days=7,
                    started_at=now - timedelta(seconds=1),
                    finished_at=now,
                    status=SourceRunStatus.FAILED,
                    stage="fetch",
                    http_status=self.status_code,
                    fetched_count=0,
                    error_code=f"http_{self.status_code}",
                    error_summary="HTTPStatusError",
                    retry_after_seconds=self.retry_after_seconds,
                    duration_ms=1000,
                ),
            ),
        )


def test_live_task_returns_collection_metadata_and_ranked_topics(tmp_path):
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'live.db'}",
        live_collector=FixtureLiveCollector(),
    )

    with TestClient(app) as client:
        response = client.post("/api/tasks/live")
        task = response.json()
        topics = client.get(f"/api/tasks/{task['id']}/topics").json()
        sources = client.get("/api/v1/sources").json()
        runs = client.get("/api/v1/sources/fixture:live/runs").json()
        reliability = client.get("/api/v1/sources/reliability?days=7").json()

    assert response.status_code == 201
    assert task["mode"] == "live"
    assert task["status"] == "review_ready"
    assert task["collected_article_count"] == 20
    assert task["window_days"] == 30
    assert task["expanded_categories"] == ["news"]
    assert task["collection_warnings"] == ["公众号索引无可见后台阅读量。"]
    assert task["source_counts"] == {"fixture-live": 20}
    assert len(topics["items"]) == 10
    assert sources["count"] == 1
    assert sources["items"][0]["health"] == "healthy"
    assert sources["items"][0]["successRate7d"] == 1.0
    assert runs["count"] == 1
    assert runs["items"][0]["status"] == "success"
    assert runs["items"][0]["stage"] == "complete"
    assert runs["items"][0]["taskId"] == task["id"]
    assert runs["items"][0]["windowDays"] == 30
    assert runs["items"][0]["fetchedCount"] == 20
    assert runs["items"][0]["storedCount"] == 20
    assert reliability["windowDays"] == 7
    assert reliability["sourceCount"] == 1
    assert reliability["sourcesWithSamples"] == 1
    assert reliability["windowComplete"] is False
    assert reliability["items"][0]["sourceId"] == "fixture:live"
    assert reliability["items"][0]["attempts"] == 1
    assert reliability["items"][0]["successRate"] == 1.0
    assert reliability["items"][0]["status"] == "collecting"


def test_live_endpoint_can_be_explicitly_disabled(tmp_path):
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'disabled.db'}",
        enable_default_live_collector=False,
    )

    with TestClient(app) as client:
        response = client.post("/api/tasks/live")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "LIVE_COLLECTION_DISABLED"


def test_three_source_failures_open_circuit_and_surface_diagnostics(tmp_path):
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'failure-health.db'}",
        live_collector=FailingFixtureLiveCollector(),
    )

    with TestClient(app) as client:
        for _ in range(3):
            assert client.post("/api/v1/tasks/live").status_code == 201
        sources = client.get("/api/v1/sources?health=failing").json()
        runs = client.get("/api/v1/sources/fixture:live/runs").json()

    assert sources["count"] == 1
    source = sources["items"][0]
    assert source["circuitState"] == "open"
    assert source["consecutiveFailures"] == 3
    assert source["successRate7d"] == 0.0
    assert source["lastErrorCode"] == "http_timeout"
    assert runs["count"] == 3
    assert {run["errorCode"] for run in runs["items"]} == {"http_timeout"}


def test_permanent_404_opens_circuit_without_blind_retries(tmp_path):
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'permanent-404.db'}",
        live_collector=HttpFailureFixtureLiveCollector(404),
    )

    with TestClient(app) as client:
        assert client.post("/api/v1/tasks/live").status_code == 201
        source = client.get("/api/v1/sources").json()["items"][0]

    assert source["health"] == "failing"
    assert source["circuitState"] == "open"
    assert source["consecutiveFailures"] == 3
    assert source["lastErrorCode"] == "http_404"


def test_retry_after_is_persisted_and_delays_next_run(tmp_path):
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'retry-after.db'}",
        live_collector=HttpFailureFixtureLiveCollector(
            429,
            retry_after_seconds=7200,
        ),
    )

    with TestClient(app) as client:
        assert client.post("/api/v1/tasks/live").status_code == 201
        source = client.get("/api/v1/sources").json()["items"][0]
        run = client.get(
            "/api/v1/sources/fixture:live/runs"
        ).json()["items"][0]

    last_checked = datetime.fromisoformat(source["lastCheckedAt"])
    next_run = datetime.fromisoformat(source["nextRunAt"])
    assert (next_run - last_checked).total_seconds() >= 7200
    assert source["consecutiveFailures"] == 1
    assert run["retryAfterSeconds"] == 7200


def test_dashboard_and_health_are_available_without_starting_a_task():
    app = create_app(enable_default_live_collector=False)

    with TestClient(app) as client:
        dashboard = client.get("/")
        health = client.get("/health")

    assert dashboard.status_code == 200
    assert 'id="root"' in dashboard.text
    assert health.json() == {"status": "ok", "version": "1.0.0"}


def test_default_collectors_seed_source_registry_without_network_calls(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("WECHAT_AGENT_ENABLE_WECHAT_INDEX", "1")
    database_path = tmp_path / "source-seed.db"
    app = create_app(
        database_url=f"sqlite:///{database_path}",
        live_collector=build_default_live_collector(),
        enable_default_live_collector=False,
    )

    with TestClient(app) as client:
        assert client.get("/api/v1/items").status_code == 200

    with sqlite3.connect(database_path) as connection:
        source_count = connection.execute("SELECT count(*) FROM sources").fetchone()[0]
        enabled_source_count = connection.execute(
            "SELECT count(*) FROM sources WHERE enabled = 1"
        ).fetchone()[0]
        signal_only_count = connection.execute(
            "SELECT count(*) FROM sources WHERE signal_only = 1"
        ).fetchone()[0]
        openai_publishers = connection.execute(
            "SELECT count(*) FROM sources WHERE publisher_group = 'official:openai'"
        ).fetchone()[0]

    assert source_count == 36
    assert enabled_source_count == 24
    assert signal_only_count == 3
    assert openai_publishers == 2
