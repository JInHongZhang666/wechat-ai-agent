from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wechat_agent.main import create_app


ACCEPTANCE_FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "acceptance_articles.json"
)


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("WECHAT_AGENT_LLM_API_KEY", "host-credential-must-not-leak")
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'acceptance.db'}",
        fixture_path=ACCEPTANCE_FIXTURE,
        load_llm_from_environment=False,
    )
    with TestClient(app) as test_client:
        yield test_client


def test_full_demo_flow_tracks_quality_ready_completion_and_edit_rollback(client):
    created = client.post("/api/tasks/demo")
    assert created.status_code == 201
    task_id = created.json()["id"]

    task = client.get(f"/api/tasks/{task_id}")
    topics_response = client.get(f"/api/tasks/{task_id}/topics")
    topics = topics_response.json()["items"]

    assert task.json()["status"] == "review_ready"
    assert topics_response.status_code == 200
    assert len(topics) == 10
    assert sum(item["category"] == "tool" for item in topics) == 5
    assert sum(item["category"] == "news" for item in topics) == 5
    assert all(item["id"].endswith("-main") for item in topics)

    drafts = []
    for selected in topics:
        evidence_response = client.get(
            f"/api/topics/{selected['id']}/evidence"
        )
        evidence = evidence_response.json()

        assert evidence_response.status_code == 200
        assert evidence["ready_for_draft"] is True
        assert len(evidence["facts"]) >= 1
        assert len(evidence["source_urls"]) >= 2
        sources = list(evidence["facts"][0]["sources"].values())
        assert len({source["source_identity"] for source in sources}) >= 2
        assert any(
            source["source_type"] in {"official", "primary"}
            and source["source_type_verified"] is True
            and source["provenance"]
            for source in sources
        )

        draft_response = client.post(
            f"/api/topics/{selected['id']}/draft", json={}
        )
        draft = draft_response.json()

        assert draft_response.status_code == 201
        assert len(draft["titles"]) >= 5
        assert draft["quality"]["blocking_issue_count"] == 0
        assert draft["quality"]["can_review"] is True
        assert draft["quality"]["can_export"] is True
        assert draft["cited_source_urls"]
        drafts.append(draft)

    completed = client.get(f"/api/tasks/{task_id}").json()
    assert completed["status"] == "completed"

    draft = drafts[0]
    exported = client.get(
        f"/api/drafts/{draft['id']}/export?format=markdown"
    )
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("text/markdown")
    assert exported.text.startswith(f"# {draft['selected_title']}")

    untrusted_title_response = client.put(
        f"/api/drafts/{draft['id']}",
        json={
            "selected_title": "该项目确认永久停止全部服务",
            "body": draft["body"],
        },
    )
    untrusted_title = untrusted_title_response.json()

    assert untrusted_title_response.status_code == 200
    assert untrusted_title["version"] == 2
    assert "edited_title_requires_verification" in {
        issue["code"] for issue in untrusted_title["quality"]["issues"]
    }
    assert "edited_content_requires_verification" not in {
        issue["code"] for issue in untrusted_title["quality"]["issues"]
    }
    assert untrusted_title["quality"]["can_review"] is False
    assert untrusted_title["quality"]["can_export"] is False
    assert client.get(f"/api/tasks/{task_id}").json()["status"] == "review_ready"

    restored_title_response = client.put(
        f"/api/drafts/{draft['id']}",
        json={
            "selected_title": draft["titles"][1],
            "body": draft["body"],
        },
    )
    restored_title = restored_title_response.json()

    assert restored_title_response.status_code == 200
    assert restored_title["version"] == 3
    assert restored_title["quality"]["blocking_issue_count"] == 0
    assert client.get(f"/api/tasks/{task_id}").json()["status"] == "completed"

    edited_response = client.put(
        f"/api/drafts/{draft['id']}",
        json={
            "selected_title": draft["titles"][1],
            "body": "该项目已经永久关闭所有服务，用户数据已全部泄露。",
        },
    )
    edited = edited_response.json()

    assert edited_response.status_code == 200
    assert edited["version"] == 4
    assert edited["available_versions"] == [1, 2, 3, 4]
    assert edited["selected_title"] == draft["titles"][1]
    assert edited["cited_source_urls"] == draft["cited_source_urls"]
    assert "edited_content_requires_verification" in {
        issue["code"] for issue in edited["quality"]["issues"]
    }
    assert edited["quality"]["can_review"] is False
    assert edited["quality"]["can_export"] is False
    assert client.get(f"/api/tasks/{task_id}").json()["status"] == "review_ready"

    restored_response = client.put(
        f"/api/drafts/{draft['id']}",
        json={
            "selected_title": draft["titles"][1],
            "body": draft["body"],
        },
    )
    restored = restored_response.json()

    assert restored_response.status_code == 200
    assert restored["version"] == 5
    assert restored["quality"]["blocking_issue_count"] == 0
    assert restored["quality"]["can_review"] is True
    assert restored["quality"]["can_export"] is True
    assert client.get(f"/api/tasks/{task_id}").json()["status"] == "completed"
