import json
import sqlite3
from datetime import UTC, datetime, timedelta
from xml.etree import ElementTree

from fastapi.testclient import TestClient

from wechat_agent.domain.models import ArticleRecord, TopicCategory
from wechat_agent.main import create_app
from wechat_agent.services.pipeline import DISTRIBUTION_STRATEGY_VERSION


def _write_fixture(path):
    now = datetime.now(UTC)
    articles = []
    categories = [
        *(TopicCategory.INDUSTRY for _ in range(7)),
        TopicCategory.AI_MODELS,
        TopicCategory.AI_PRODUCTS,
        TopicCategory.PAPER,
        TopicCategory.TIP,
    ]
    for index, category in enumerate(categories):
        articles.append(
            ArticleRecord(
                id=f"recent-{index}",
                title=f"Recent {category.value} {index}",
                source_name=f"Source {index}",
                source_url=f"https://example.com/recent-{index}",
                published_at=now - timedelta(hours=index + 1),
                collected_at=now,
                summary=f"Summary for {category.value} {index}",
                category=category,
                tier="t1",
            )
        )
    articles.append(
        ArticleRecord(
            id="old-tip",
            title="Old tip",
            source_name="Old Source",
            source_url="https://example.com/old-tip",
            published_at=now - timedelta(days=10),
            collected_at=now,
            summary="Outside the seven day window",
            category=TopicCategory.TIP,
            tier="t2",
        )
    )
    path.write_text(
        json.dumps(
            [article.model_dump(mode="json") for article in articles],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_v1_items_honor_mode_category_window_cursor_and_etag(tmp_path):
    fixture_path = tmp_path / "v1-articles.json"
    _write_fixture(fixture_path)
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'v1.db'}",
        fixture_path=fixture_path,
        enable_default_live_collector=False,
    )

    with TestClient(app) as client:
        assert client.post("/api/v1/tasks/demo").status_code == 201

        first = client.get("/api/v1/items?mode=all&window=7d&limit=3")
        first_payload = first.json()
        not_modified = client.get(
            "/api/v1/items?mode=all&window=7d&limit=3",
            headers={"If-None-Match": first.headers["etag"]},
        )
        fixture_payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        inserted = ArticleRecord(
            id="inserted-after-page-one",
            title="Inserted after page one",
            source_name="New Source",
            source_url="https://example.com/inserted-after-page-one",
            published_at=datetime.now(UTC),
            collected_at=datetime.now(UTC),
            summary="A newly inserted item must not shift the keyset cursor.",
            category=TopicCategory.INDUSTRY,
            tier="t1",
        )
        fixture_payload.append(inserted.model_dump(mode="json"))
        fixture_path.write_text(
            json.dumps(fixture_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        assert client.post("/api/v1/tasks/demo").status_code == 201
        second = client.get(
            "/api/v1/items?mode=all&window=7d&limit=3"
            f"&cursor={first_payload['page']['nextCursor']}"
        )
        wrong_query = client.get(
            "/api/v1/items?mode=all&window=24h&limit=3"
            f"&cursor={first_payload['page']['nextCursor']}"
        )
        published = client.get(
            "/api/v1/items?mode=all&window=7d&by=published&limit=100"
        )
        selected = client.get("/api/v1/items?mode=selected&window=7d&limit=100")
        industry = client.get(
            "/api/v1/items?mode=all&window=7d&category=industry&limit=100"
        )
        models = client.get(
            "/api/v1/items?mode=all&window=7d&category=ai-models&limit=100"
        )

    assert first_payload["page"] == {
        "count": 3,
        "hasMore": True,
        "nextCursor": first_payload["page"]["nextCursor"],
        "cursorVersion": 1,
    }
    assert first_payload["page"]["nextCursor"]
    assert {item["id"] for item in first_payload["items"]}.isdisjoint(
        {item["id"] for item in second.json()["items"]}
    )
    assert "inserted-after-page-one" not in {
        item["id"] for item in second.json()["items"]
    }
    assert wrong_query.status_code == 400
    assert wrong_query.json()["code"] == "INVALID_CURSOR"
    assert first_payload["query"]["by"] == "timeline"
    assert first_payload["query"]["anchorAt"]
    assert all(item["timelineAt"] for item in first_payload["items"])
    assert published.json()["query"]["by"] == "published"
    assert [item["publishedAt"] for item in published.json()["items"]] == sorted(
        [item["publishedAt"] for item in published.json()["items"]],
        reverse=True,
    )
    assert len(selected.json()["items"]) == 10
    assert all(item["selected"] for item in selected.json()["items"])
    assert len(industry.json()["items"]) == 8
    assert {item["category"] for item in industry.json()["items"]} == {"industry"}
    assert len(models.json()["items"]) == 1
    assert models.json()["items"][0]["score"] is not None
    assert models.json()["items"][0]["reason"]
    assert (
        models.json()["items"][0]["assessment"]["strategyVersion"]
        == DISTRIBUTION_STRATEGY_VERSION
    )
    assert models.json()["items"][0]["source"]["tier"] == "T1"
    assert not_modified.status_code == 304
    assert not_modified.content == b""
    assert "content-length" not in not_modified.headers


def test_rss_is_valid_uses_five_categories_and_daily_reports(tmp_path):
    fixture_path = tmp_path / "rss-articles.json"
    _write_fixture(fixture_path)
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'rss.db'}",
        fixture_path=fixture_path,
        enable_default_live_collector=False,
    )

    with TestClient(app) as client:
        client.post("/api/v1/tasks/demo")
        category_feed = client.get("/feed/category/industry.xml")
        daily_feed = client.get("/feed/daily.xml")

    category_root = ElementTree.fromstring(category_feed.content)
    daily_root = ElementTree.fromstring(daily_feed.content)
    category_items = category_root.findall("./channel/item")
    daily_items = daily_root.findall("./channel/item")

    assert category_feed.status_code == 200
    assert len(category_items) == 6
    assert {item.findtext("category") for item in category_items} == {"industry"}
    assert all(item.findtext("pubDate", "").endswith("GMT") for item in category_items)
    assert b"&lt;![CDATA[" not in category_feed.content
    assert daily_feed.status_code == 200
    # The report boundary is Asia/Shanghai rather than UTC, so the recent
    # fixture rows belong to one local calendar day.
    assert len(daily_items) >= 1
    assert all(item.findtext("guid", "").startswith("daily:") for item in daily_items)


def test_feed_survives_without_task_records(tmp_path):
    fixture_path = tmp_path / "persistent-feed.json"
    database_path = tmp_path / "persistent-feed.db"
    _write_fixture(fixture_path)

    first_app = create_app(
        database_url=f"sqlite:///{database_path}",
        fixture_path=fixture_path,
        enable_default_live_collector=False,
    )
    with TestClient(first_app) as client:
        assert client.post("/api/v1/tasks/demo").status_code == 201
        before = client.get("/api/v1/items?mode=all&window=7d").json()["items"]

    with sqlite3.connect(database_path) as connection:
        connection.execute("DELETE FROM tasks")

    restarted_app = create_app(
        database_url=f"sqlite:///{database_path}",
        fixture_path=fixture_path,
        enable_default_live_collector=False,
    )
    with TestClient(restarted_app) as client:
        after = client.get("/api/v1/items?mode=all&window=7d").json()["items"]

    assert after == before
