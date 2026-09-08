import json
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.story_factories import make_story_article
from wechat_agent.api.errors import AppError, app_error_handler
from wechat_agent.api.story_shadow import create_story_shadow_router
from wechat_agent.main import create_app


def _app(tmp_path):
    fixture_path = tmp_path / "empty.json"
    fixture_path.write_text("[]", encoding="utf-8")
    return create_app(
        database_url=f"sqlite:///{tmp_path / 'story-api.db'}",
        fixture_path=fixture_path,
        enable_default_live_collector=False,
    )


def test_story_shadow_endpoints_are_internal(tmp_path):
    app = _app(tmp_path)

    with TestClient(app) as client:
        status = client.get("/api/ops/story-shadow/status")
        schema = client.get("/openapi-v1.json").json()

    assert status.status_code == 200
    assert status.json()["strategyVersion"] == "story-shadow-v1"
    assert not any("story-shadow" in path for path in schema["paths"])


def test_story_shadow_replay_is_bounded_and_query_limits_are_validated(tmp_path):
    app = _app(tmp_path)

    with TestClient(app) as client:
        too_many_days = client.post(
            "/api/ops/story-shadow/replay",
            json={"days": 8},
        )
        too_many_rows = client.get(
            "/api/ops/story-shadow/stories",
            params={"limit": 101},
        )
        replay = client.post(
            "/api/ops/story-shadow/replay",
            json={"days": 7},
        )

    assert too_many_days.status_code == 422
    assert too_many_rows.status_code == 422
    assert replay.status_code == 200
    assert replay.json()["strategyVersion"] == "story-shadow-v1"
    assert replay.json()["status"] == "success"


def test_story_shadow_returns_problem_json_when_sqlite_service_is_unavailable():
    app = FastAPI()
    app.include_router(create_story_shadow_router(lambda: None))
    app.add_exception_handler(AppError, app_error_handler)

    with TestClient(app) as client:
        response = client.get("/api/ops/story-shadow/status")

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "STORY_SHADOW_UNAVAILABLE"


def test_story_shadow_missing_id_returns_stable_problem_code(tmp_path):
    app = _app(tmp_path)

    with TestClient(app) as client:
        response = client.get("/api/ops/story-shadow/stories/missing")

    assert response.status_code == 404
    assert response.json()["code"] == "STORY_NOT_FOUND"


def test_story_replay_does_not_change_public_or_creation_outputs(tmp_path):
    now = datetime.now(UTC)
    fixture_path = tmp_path / "story-isolation.json"
    articles = [
        make_story_article(
            id="isolation-official",
            source_id="official:isolation",
            source_identity="official:isolation",
            source_type="official",
            tier="t1",
            published_at=now - timedelta(hours=2),
            collected_at=now,
        ),
        make_story_article(
            id="isolation-coverage",
            source_id="publisher:isolation",
            source_identity="publisher:isolation",
            published_at=now - timedelta(hours=1),
            collected_at=now,
        ),
    ]
    fixture_path.write_text(
        json.dumps(
            [article.model_dump(mode="json") for article in articles],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'story-isolation.db'}",
        fixture_path=fixture_path,
        enable_default_live_collector=False,
    )

    def snapshot(client):
        return {
            "hotTopics": client.get("/api/v1/hot-topics").json(),
            "dailies": client.get("/api/v1/dailies").json(),
            "dailyRss": client.get("/feed/daily.xml").text,
            "opportunities": client.get(
                "/api/v1/creation/opportunities"
            ).json(),
        }

    with TestClient(app) as client:
        assert client.post("/api/tasks/demo").status_code == 201
        before = snapshot(client)
        replay = client.post(
            "/api/ops/story-shadow/replay",
            json={"days": 7},
        )
        after = snapshot(client)
        status = client.get("/api/ops/story-shadow/status").json()

    assert replay.status_code == 200
    assert status["storyCount"] >= 1
    assert after == before
