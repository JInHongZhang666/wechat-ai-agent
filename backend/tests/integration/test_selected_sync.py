import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from wechat_agent.api.public_schemas import (
    SelectedChangesResponse,
    SelectedSnapshotResponse,
)
from wechat_agent.domain.models import ArticleRecord, TopicCategory
from wechat_agent.main import create_app


def _write_fixture(path: Path) -> None:
    now = datetime.now(UTC)
    categories = [
        *(TopicCategory.INDUSTRY for _ in range(7)),
        TopicCategory.AI_MODELS,
        TopicCategory.AI_PRODUCTS,
        TopicCategory.PAPER,
        TopicCategory.TIP,
    ]
    articles = [
        ArticleRecord(
            id=f"sync-{index}",
            title=f"Sync {category.value} {index}",
            source_name=f"Sync Source {index}",
            source_url=f"https://example.com/sync-{index}",
            published_at=now - timedelta(hours=index + 1),
            collected_at=now,
            summary=f"Summary for {category.value} {index}",
            category=category,
            tier="t1",
        )
        for index, category in enumerate(categories)
    ]
    path.write_text(
        json.dumps(
            [article.model_dump(mode="json") for article in articles],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _app(database_path: Path, fixture_path: Path):
    return create_app(
        database_url=f"sqlite:///{database_path}",
        fixture_path=fixture_path,
        enable_default_live_collector=False,
    )


def _seed(client: TestClient) -> None:
    response = client.post("/api/v1/tasks/demo")
    assert response.status_code == 201


def _complete_snapshot(
    client: TestClient,
    *,
    fields: str = "minimal",
    limit: int = 2,
) -> tuple[dict[str, dict], str, str | None]:
    response = client.get(
        "/api/v1/selected/snapshot",
        params={"fields": fields, "limit": limit},
    )
    assert response.status_code == 200
    SelectedSnapshotResponse.model_validate(response.json())
    payload = response.json()
    cursor = payload["cursor"]
    first_next_page = payload["nextPage"]
    mirror = {item["id"]: item for item in payload["items"]}
    while payload["hasMore"]:
        response = client.get(
            "/api/v1/selected/snapshot",
            params={
                "fields": fields,
                "limit": limit,
                "page": payload["nextPage"],
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["cursor"] == cursor
        assert set(mirror).isdisjoint(
            {item["id"] for item in payload["items"]}
        )
        mirror.update({item["id"]: item for item in payload["items"]})
    return mirror, cursor, first_next_page


def test_snapshot_changes_remove_recovery_and_etag_contract(tmp_path):
    database_path = tmp_path / "selected-sync.db"
    fixture_path = tmp_path / "selected-sync.json"
    _write_fixture(fixture_path)
    app = _app(database_path, fixture_path)

    with TestClient(app) as client:
        _seed(client)
        first = client.get(
            "/api/v1/selected/snapshot",
            params={"fields": "minimal", "limit": 2},
        )
        cached = client.get(
            "/api/v1/selected/snapshot",
            params={"fields": "minimal", "limit": 2},
            headers={"If-None-Match": first.headers["etag"]},
        )
        mirror, cursor, snapshot_page = _complete_snapshot(client)

        assert len(mirror) >= 3
        assert cached.status_code == 304
        assert cached.content == b""
        assert all("summary" not in item for item in mirror.values())
        assert all(set(item["links"]) == {"aihot"} for item in mirror.values())
        assert snapshot_page is not None

        remove_id, upsert_id = list(mirror)[:2]
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                "UPDATE curation_decisions SET selected = 0 WHERE article_id = ?",
                (remove_id,),
            )
            connection.execute(
                "UPDATE articles SET title = title || ' · updated' WHERE id = ?",
                (upsert_id,),
            )

        changes_seen = []
        current_cursor = cursor
        for _ in range(20):
            response = client.get(
                "/api/v1/selected/changes",
                params={"cursor": current_cursor, "limit": 1},
            )
            assert response.status_code == 200
            SelectedChangesResponse.model_validate(response.json())
            payload = response.json()
            changes_seen.extend(payload["changes"])
            for change in payload["changes"]:
                if change["op"] == "upsert":
                    mirror[change["id"]] = change["item"]
                else:
                    mirror.pop(change["id"], None)
            current_cursor = payload["cursor"]
            if not payload["hasMore"]:
                break
        else:
            raise AssertionError("changes backlog did not drain")

        assert any(
            change["op"] == "remove" and change["id"] == remove_id
            for change in changes_seen
        )
        assert any(
            change["op"] == "upsert" and change["id"] == upsert_id
            for change in changes_seen
        )
        assert mirror[upsert_id]["title"].endswith(" · updated")

        rebuilt, rebuilt_cursor, _ = _complete_snapshot(
            client,
            fields="minimal",
            limit=1000,
        )
        assert mirror == rebuilt
        assert rebuilt_cursor == current_cursor

        empty = client.get(
            "/api/v1/selected/changes",
            params={"cursor": current_cursor, "limit": 100},
        )
        empty_cached = client.get(
            "/api/v1/selected/changes",
            params={"cursor": current_cursor, "limit": 100},
            headers={"If-None-Match": empty.headers["etag"]},
        )
        mixed_change_cursor = client.get(
            "/api/v1/selected/changes",
            params={"cursor": snapshot_page},
        )
        mixed_page_cursor = client.get(
            "/api/v1/selected/snapshot",
            params={
                "fields": "minimal",
                "limit": 2,
                "page": current_cursor,
            },
        )

    assert empty.json()["changes"] == []
    assert empty_cached.status_code == 304
    assert mixed_change_cursor.status_code == 409
    assert mixed_change_cursor.json()["code"] == "SNAPSHOT_REQUIRED"
    assert mixed_page_cursor.status_code == 409
    assert mixed_page_cursor.json()["code"] == "SNAPSHOT_REQUIRED"


def test_frozen_snapshot_page_survives_restart_and_expires_boundedly(tmp_path):
    database_path = tmp_path / "selected-restart.db"
    fixture_path = tmp_path / "selected-restart.json"
    _write_fixture(fixture_path)
    first_app = _app(database_path, fixture_path)
    with TestClient(first_app) as client:
        _seed(client)
        first = client.get(
            "/api/v1/selected/snapshot",
            params={"fields": "default", "limit": 1},
        ).json()
        assert first["hasMore"] is True
        page = first["nextPage"]
        first_id = first["items"][0]["id"]

    with sqlite3.connect(database_path) as connection:
        target_id, original_title = connection.execute(
            "SELECT a.id, a.title FROM articles a "
            "JOIN curation_decisions c ON c.article_id = a.id "
            "WHERE c.selected = 1 AND a.id <> ? LIMIT 1",
            (first_id,),
        ).fetchone()
        connection.execute(
            "UPDATE articles SET title = title || ' · after-snapshot' WHERE id = ?",
            (target_id,),
        )

    restarted_app = _app(database_path, fixture_path)
    with TestClient(restarted_app) as client:
        frozen_items = []
        next_page = page
        while next_page is not None:
            response = client.get(
                "/api/v1/selected/snapshot",
                params={
                    "fields": "default",
                    "limit": 1,
                    "page": next_page,
                },
            )
            assert response.status_code == 200
            payload = response.json()
            frozen_items.extend(payload["items"])
            next_page = payload["nextPage"]
        frozen_target = next(
            item for item in frozen_items if item["id"] == target_id
        )
        assert frozen_target["title"] == original_title

        fresh = client.get(
            "/api/v1/selected/snapshot",
            params={"fields": "minimal", "limit": 1},
        ).json()
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                "UPDATE selected_sync_snapshots "
                "SET expires_at = '2000-01-01T00:00:00+00:00' "
                "WHERE cursor_token = ? AND fields = 'minimal' AND page_limit = 1",
                (fresh["cursor"],),
            )
        expired = client.get(
            "/api/v1/selected/snapshot",
            params={
                "fields": "minimal",
                "limit": 1,
                "page": fresh["nextPage"],
            },
        )

    assert expired.status_code == 409
    assert expired.json()["code"] == "SNAPSHOT_REQUIRED"


def test_changes_cursor_expires_after_retention_floor(tmp_path, monkeypatch):
    monkeypatch.setenv("WECHAT_AGENT_SELECTED_CHANGE_RETENTION", "2")
    database_path = tmp_path / "selected-retention.db"
    fixture_path = tmp_path / "selected-retention.json"
    _write_fixture(fixture_path)
    app = _app(database_path, fixture_path)

    with TestClient(app) as client:
        _seed(client)
        mirror, cursor, _ = _complete_snapshot(
            client,
            fields="minimal",
            limit=1000,
        )
        changed_ids = list(mirror)[:3]
        assert len(changed_ids) == 3
        with sqlite3.connect(database_path) as connection:
            for item_id in changed_ids:
                connection.execute(
                    "UPDATE articles SET title = title || ' · retained' WHERE id = ?",
                    (item_id,),
                )
        expired = client.get(
            "/api/v1/selected/changes",
            params={"cursor": cursor, "limit": 100},
        )

    assert expired.status_code == 409
    assert expired.json()["code"] == "SNAPSHOT_REQUIRED"
