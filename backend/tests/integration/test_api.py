from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wechat_agent.main import create_app
from wechat_agent.services.pipeline import TaskStatus


FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "articles.json"


@pytest.fixture
def client(tmp_path) -> TestClient:
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'api.db'}",
        fixture_path=FIXTURE_PATH,
    )
    with TestClient(app) as test_client:
        yield test_client


def _create_demo(client: TestClient) -> tuple[str, list[dict]]:
    created = client.post("/api/tasks/demo")
    assert created.status_code == 201
    task_id = created.json()["id"]
    topics = client.get(f"/api/tasks/{task_id}/topics").json()["items"]
    return task_id, topics


def test_task_status_enum_is_the_exact_phase_one_contract():
    assert {status.value for status in TaskStatus} == {
        "queued",
        "collecting",
        "pre_screening",
        "ranking",
        "review_ready",
        "drafting",
        "completed",
        "failed",
    }


def test_application_metadata_requires_a_single_uvicorn_worker():
    app = create_app()

    assert "single-process" in app.description
    assert "uvicorn --workers 1" in app.description


def test_demo_task_reaches_review_ready_and_returns_ten_topics(client):
    task_id, topics = _create_demo(client)

    task_response = client.get(f"/api/tasks/{task_id}")
    task = task_response.json()

    assert task_response.status_code == 200
    assert task["status"] == "review_ready"
    assert task["status_history"] == [
        "queued",
        "collecting",
        "ranking",
        "review_ready",
    ]
    assert len(topics) == 10
    assert all(item["recommendation_reason"] for item in topics)


def test_partial_drafts_return_task_to_review_ready_until_every_topic_has_a_draft(
    client,
):
    task_id, topics = _create_demo(client)

    first = client.post(f"/api/topics/{topics[0]['id']}/draft", json={})
    after_first = client.get(f"/api/tasks/{task_id}").json()
    second = client.post(f"/api/topics/{topics[1]['id']}/draft", json={})
    after_second = client.get(f"/api/tasks/{task_id}").json()

    assert first.status_code == 201
    assert second.status_code == 201
    assert after_first["status"] == "review_ready"
    assert after_second["status"] == "review_ready"
    assert after_first["status_history"][-2:] == ["drafting", "review_ready"]


def test_task_does_not_complete_when_every_draft_is_quality_blocked(client):
    task_id, topics = _create_demo(client)

    for topic in topics:
        response = client.post(f"/api/topics/{topic['id']}/draft", json={})
        assert response.status_code == 201
        assert response.json()["quality"]["can_review"] is False

    task = client.get(f"/api/tasks/{task_id}").json()

    assert task["status"] == "review_ready"
    assert task["status_history"][-1] == "review_ready"


def test_generating_another_blocked_draft_keeps_review_ready_status(client):
    task_id, topics = _create_demo(client)
    for topic in topics:
        assert client.post(f"/api/topics/{topic['id']}/draft", json={}).status_code == 201

    before = client.get(f"/api/tasks/{task_id}").json()
    repeated = client.post(f"/api/topics/{topics[0]['id']}/draft", json={})
    after = client.get(f"/api/tasks/{task_id}").json()

    assert repeated.status_code == 201
    assert after["status"] == "review_ready"
    assert after["status_history"][-1] == "review_ready"
    assert after["status_history"].count("completed") == 0
    assert len(after["status_history"]) >= len(before["status_history"])


def test_evidence_and_draft_keep_real_source_blockers_instead_of_fabricating(
    client,
):
    _, topics = _create_demo(client)
    topic_id = topics[0]["id"]

    evidence_response = client.get(f"/api/topics/{topic_id}/evidence")
    draft_response = client.post(
        f"/api/topics/{topic_id}/draft",
        json={"account_name": "AI信息小屋"},
    )
    evidence = evidence_response.json()
    draft = draft_response.json()

    assert evidence_response.status_code == 200
    assert evidence["topic_id"] == topic_id
    assert evidence["ready_for_draft"] is False
    assert draft_response.status_code == 201
    assert draft["stage"] == "research"
    assert len(draft["titles"]) == 5
    assert draft["quality"]["blocking_issue_count"] > 0
    assert draft["quality"]["can_review"] is False
    assert draft["quality"]["can_export"] is False
    assert "insufficient_sources" in {
        issue["code"] for issue in draft["quality"]["issues"]
    }


def test_editing_draft_creates_version_and_reruns_quality_without_new_citations(
    client,
):
    _, topics = _create_demo(client)
    created = client.post(f"/api/topics/{topics[0]['id']}/draft", json={}).json()

    updated_response = client.put(
        f"/api/drafts/{created['id']}",
        json={
            "selected_title": "这是一个新的完整标题候选",
            "body": "仍可继续编辑的研究正文。",
        },
    )
    updated = updated_response.json()

    assert updated_response.status_code == 200
    assert updated["version"] == 2
    assert updated["available_versions"] == [1, 2]
    assert updated["selected_title"] == "这是一个新的完整标题候选"
    assert updated["body"] == "仍可继续编辑的研究正文。"
    assert updated["cited_source_urls"] == created["cited_source_urls"]
    assert updated["quality"]["blocking_issue_count"] > 0
    assert updated["quality"]["can_export"] is False


def test_title_only_edit_allows_generated_choice_but_blocks_free_form_title(
    client,
):
    _, topics = _create_demo(client)
    created = client.post(f"/api/topics/{topics[0]['id']}/draft", json={}).json()

    generated_choice = client.put(
        f"/api/drafts/{created['id']}",
        json={
            "selected_title": created["titles"][1],
            "body": created["body"],
        },
    ).json()
    free_form = client.put(
        f"/api/drafts/{created['id']}",
        json={
            "selected_title": "该项目确认永久停止全部服务",
            "body": created["body"],
        },
    ).json()

    assert "edited_title_requires_verification" not in {
        issue["code"] for issue in generated_choice["quality"]["issues"]
    }
    assert "edited_content_requires_verification" not in {
        issue["code"] for issue in generated_choice["quality"]["issues"]
    }
    assert "edited_title_requires_verification" in {
        issue["code"] for issue in free_form["quality"]["issues"]
    }
    assert "edited_content_requires_verification" not in {
        issue["code"] for issue in free_form["quality"]["issues"]
    }


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/tasks/not-found"),
        ("get", "/api/tasks/not-found/topics"),
        ("get", "/api/topics/not-found/evidence"),
        ("post", "/api/topics/not-found/draft"),
        ("put", "/api/drafts/not-found"),
    ],
)
def test_missing_resources_return_uniform_errors(client, method, path):
    if method == "get":
        response = client.get(path)
    elif method == "put":
        response = client.put(
            path,
            json={"selected_title": "有效标题", "body": "可编辑正文"},
        )
    else:
        response = client.post(path, json={})

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "NOT_FOUND",
            "message": "请求的资源不存在。",
            "retryable": False,
        }
    }


def test_invalid_writing_profile_returns_uniform_error(client):
    _, topics = _create_demo(client)

    response = client.post(
        f"/api/topics/{topics[0]['id']}/draft",
        json={"target_characters": 10},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"
    assert response.json()["error"]["retryable"] is False


@pytest.mark.parametrize(
    ("method", "path", "expected_status", "expected_code"),
    [
        ("get", "/api/not-a-route", 404, "NOT_FOUND"),
        ("post", "/api/tasks/not-found", 405, "METHOD_NOT_ALLOWED"),
    ],
)
def test_framework_http_errors_use_uniform_error_shape(
    client, method, path, expected_status, expected_code
):
    response = getattr(client, method)(path)

    assert response.status_code == expected_status
    assert response.json()["error"] == {
        "code": expected_code,
        "message": (
            "请求的资源不存在。"
            if expected_status == 404
            else "请求方法不受支持。"
        ),
        "retryable": False,
    }


def test_internal_demo_failure_returns_uniform_error(tmp_path):
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'api.db'}",
        fixture_path=tmp_path / "missing.json",
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/tasks/demo")

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "INTERNAL_FAILURE",
            "message": "演示任务执行失败。",
            "retryable": True,
        }
    }


def test_separate_apps_do_not_share_in_memory_task_state(tmp_path):
    first_app = create_app(
        database_url=f"sqlite:///{tmp_path / 'first.db'}",
        fixture_path=FIXTURE_PATH,
    )
    second_app = create_app(
        database_url=f"sqlite:///{tmp_path / 'second.db'}",
        fixture_path=FIXTURE_PATH,
    )
    with TestClient(first_app) as first, TestClient(second_app) as second:
        task_id = first.post("/api/tasks/demo").json()["id"]
        response = second.get(f"/api/tasks/{task_id}")

    assert response.status_code == 404


def test_default_apps_use_isolated_memory_databases_without_creating_a_file(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("WECHAT_AGENT_DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    first_app = create_app(fixture_path=FIXTURE_PATH)
    second_app = create_app(fixture_path=FIXTURE_PATH)

    with TestClient(first_app) as first, TestClient(second_app) as second:
        task_id = first.post("/api/tasks/demo").json()["id"]
        response = second.get(f"/api/tasks/{task_id}")

    assert response.status_code == 404
    assert not (tmp_path / "wechat_agent.db").exists()


def test_repeated_demo_runs_use_distinct_api_topic_ids(client):
    first_task_id, first_topics = _create_demo(client)
    second_task_id, second_topics = _create_demo(client)

    assert first_task_id != second_task_id
    assert {item["id"] for item in first_topics}.isdisjoint(
        {item["id"] for item in second_topics}
    )
    assert client.get(
        f"/api/topics/{first_topics[0]['id']}/evidence"
    ).status_code == 200


def test_blocking_quality_report_cannot_be_bypassed_by_export(client):
    _, topics = _create_demo(client)
    draft = client.post(f"/api/topics/{topics[0]['id']}/draft", json={}).json()

    response = client.get(f"/api/drafts/{draft['id']}/export?format=markdown")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "QUALITY_BLOCKED"


def test_file_database_restores_tasks_topics_evidence_and_drafts(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'restart.db'}"
    first_app = create_app(
        database_url=database_url,
        fixture_path=FIXTURE_PATH,
        enable_default_live_collector=False,
    )
    with TestClient(first_app) as first:
        task_id, topics = _create_demo(first)
        topic_id = topics[0]["id"]
        evidence = first.get(f"/api/topics/{topic_id}/evidence").json()
        draft = first.post(f"/api/topics/{topic_id}/draft", json={}).json()

    second_app = create_app(
        database_url=database_url,
        fixture_path=FIXTURE_PATH,
        enable_default_live_collector=False,
    )
    with TestClient(second_app) as second:
        restored_task = second.get(f"/api/tasks/{task_id}")
        restored_topics = second.get(f"/api/tasks/{task_id}/topics")
        restored_evidence = second.get(f"/api/topics/{topic_id}/evidence")
        restored_draft = second.get(f"/api/v1/topics/{topic_id}/draft")

    assert restored_task.status_code == 200
    assert len(restored_topics.json()["items"]) == 10
    assert restored_evidence.json() == evidence
    assert restored_draft.status_code == 200
    assert restored_draft.json()["id"] == draft["id"]
