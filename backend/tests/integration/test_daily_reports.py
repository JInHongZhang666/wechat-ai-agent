import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree

from fastapi.testclient import TestClient

from wechat_agent.api.routes import ContentApiService
from wechat_agent.domain.models import (
    ArticleRecord,
    CurationDecision,
    HeatConfidence,
    TopicCategory,
)
from wechat_agent.main import create_app
from wechat_agent.services.daily import (
    materialize_daily_report,
    refresh_daily_reports_for_articles,
)
from wechat_agent.services.pipeline import ContentPipeline
from wechat_agent.storage.memory import (
    InMemoryArticleRepository,
    InMemoryCurationDecisionRepository,
    InMemoryDailyReportRepository,
)
from wechat_agent.writing.provider import RuleBasedDraftProvider


def _write_selected_fixture(path: Path) -> list[dict]:
    now = datetime.now(UTC)
    categories = [
        *(TopicCategory.INDUSTRY for _ in range(7)),
        TopicCategory.AI_MODELS,
        TopicCategory.AI_PRODUCTS,
        TopicCategory.PAPER,
        TopicCategory.TIP,
    ]
    payload = [
        ArticleRecord(
            id=f"daily-{index}",
            title=f"Daily {category.value} {index}",
            source_name=f"Source {index}",
            source_url=f"https://example.com/daily-{index}",
            published_at=now - timedelta(hours=index + 1),
            collected_at=now,
            summary=f"Daily summary for {category.value} {index}",
            category=category,
            tier="t1",
        ).model_dump(mode="json")
        for index, category in enumerate(categories)
    ]
    path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    return payload


def _app(database_path: Path, fixture_path: Path):
    return create_app(
        database_url=f"sqlite:///{database_path}",
        fixture_path=fixture_path,
        enable_default_live_collector=False,
    )


def test_daily_report_is_persisted_idempotent_and_restored(tmp_path):
    fixture_path = tmp_path / "daily-fixture.json"
    _write_selected_fixture(fixture_path)
    database_path = tmp_path / "daily.db"

    with TestClient(_app(database_path, fixture_path)) as client:
        assert client.post("/api/v1/tasks/demo").status_code == 201
        first = client.get("/api/v1/dailies/latest")
        first_payload = first.json()
        second = client.get("/api/v1/dailies/latest")
        index = client.get("/api/v1/dailies").json()
        unchanged = client.post(
            f"/api/v1/dailies/{first_payload['date']}/rebuild"
        ).json()
        rss = client.get("/feed/daily.xml")

    assert first.status_code == 200
    assert first_payload == second.json() == unchanged
    assert first_payload["timezone"] == "Asia/Shanghai"
    assert first_payload["revision"] == 1
    assert first_payload["status"] == "published"
    assert len(first_payload["contentHash"]) == 64
    assert first_payload["itemCount"] > 0
    assert first_payload["sourceCount"] > 0
    assert index["items"][0]["contentHash"] == first_payload["contentHash"]
    rss_root = ElementTree.fromstring(rss.content)
    assert rss_root.findtext("./channel/item/guid", "").endswith(":r1")

    with TestClient(_app(database_path, fixture_path)) as client:
        restored = client.get(
            f"/api/v1/dailies/{first_payload['date']}"
        ).json()
    assert restored == first_payload


def test_changed_ingestion_creates_one_new_daily_revision(tmp_path):
    fixture_path = tmp_path / "changing-fixture.json"
    payload = _write_selected_fixture(fixture_path)
    database_path = tmp_path / "revisions.db"

    with TestClient(_app(database_path, fixture_path)) as client:
        client.post("/api/v1/tasks/demo")
        first = client.get("/api/v1/dailies/latest").json()

        for article in payload:
            article["summary"] = f"{article['summary']} Updated evidence snapshot."
        fixture_path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        client.post("/api/v1/tasks/demo")
        revised = client.get(
            f"/api/v1/dailies/{first['date']}"
        ).json()
        idempotent = client.post(
            f"/api/v1/dailies/{first['date']}/rebuild"
        ).json()

    assert revised["revision"] == 2
    assert revised["contentHash"] != first["contentHash"]
    assert idempotent == revised
    with sqlite3.connect(database_path) as connection:
        revisions = connection.execute(
            "SELECT revision, content_hash FROM daily_reports "
            "WHERE report_date = ? ORDER BY revision",
            (first["date"],),
        ).fetchall()
    assert revisions == [
        (1, first["contentHash"]),
        (2, revised["contentHash"]),
    ]


def test_shanghai_boundary_late_article_and_empty_day_are_stable(tmp_path):
    article_repository = InMemoryArticleRepository()
    decision_repository = InMemoryCurationDecisionRepository()
    daily_repository = InMemoryDailyReportRepository()
    service = ContentApiService(
        ContentPipeline(
            article_repository,
            curation_repository=decision_repository,
            enable_pre_screen=False,
        ),
        tmp_path / "unused.json",
        RuleBasedDraftProvider(),
        daily_report_repo=daily_repository,
    )

    before_midnight = ArticleRecord(
        id="before-midnight",
        title="Before Shanghai midnight",
        source_name="Boundary Source",
        source_url="https://example.com/before-midnight",
        published_at=datetime(2026, 8, 1, 15, 59, tzinfo=UTC),
        collected_at=datetime(2026, 8, 2, 1, tzinfo=UTC),
        summary="Belongs to the August 1 Shanghai report.",
        category=TopicCategory.INDUSTRY,
        tier="t1",
    )
    after_midnight = before_midnight.model_copy(
        update={
            "id": "after-midnight",
            "title": "After Shanghai midnight",
            "source_url": "https://example.com/after-midnight",
            "published_at": datetime(2026, 8, 1, 16, 1, tzinfo=UTC),
            "summary": "Belongs to the August 2 Shanghai report.",
        }
    )

    def selected(article: ArticleRecord, scored_at: datetime):
        return CurationDecision(
            article_id=article.id,
            distribution_score=90,
            selected=True,
            eligible=True,
            threshold=60,
            reason="Boundary test selection.",
            score_confidence=HeatConfidence.MEASURED,
            strategy_version="distribution-test",
            scored_at=scored_at,
        )

    article_repository.save_many([before_midnight, after_midnight])
    decision_repository.save_many(
        [
            selected(before_midnight, before_midnight.collected_at),
            selected(after_midnight, after_midnight.collected_at),
        ]
    )

    august_1 = materialize_daily_report(
        service,
        date(2026, 8, 1),
        rebuild=True,
    )
    august_2 = materialize_daily_report(
        service,
        date(2026, 8, 2),
        rebuild=True,
    )
    assert august_1["itemCount"] == 1
    assert august_1["sections"][0]["items"][0]["id"] == "before-midnight"
    assert august_2["itemCount"] == 1
    assert august_2["sections"][0]["items"][0]["id"] == "after-midnight"

    late_article = after_midnight.model_copy(
        update={
            "id": "late-article",
            "title": "Late arrival",
            "source_url": "https://example.com/late-article",
            "published_at": datetime(2026, 8, 1, 18, tzinfo=UTC),
            "collected_at": datetime(2026, 8, 3, 1, tzinfo=UTC),
        }
    )
    article_repository.save_many([late_article])
    decision_repository.save_many(
        [selected(late_article, late_article.collected_at)]
    )
    refresh_daily_reports_for_articles(service, [late_article])
    revised = daily_repository.get(date(2026, 8, 2))
    assert revised is not None
    assert revised.revision == 2
    assert revised.item_count == 2

    empty_first = materialize_daily_report(
        service,
        date(2026, 8, 3),
        rebuild=True,
    )
    empty_second = materialize_daily_report(
        service,
        date(2026, 8, 3),
        rebuild=True,
    )
    assert empty_first == empty_second
    assert empty_first["itemCount"] == 0
    assert empty_first["revision"] == 1
