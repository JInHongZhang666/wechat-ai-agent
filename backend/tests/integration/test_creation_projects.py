from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from wechat_agent.main import create_app


ACCEPTANCE_FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "acceptance_articles.json"
)


@pytest.fixture(autouse=True)
def _disable_external_llm_for_creation_integration_tests(monkeypatch):
    """Keep integration tests deterministic and prevent accidental API spend."""
    monkeypatch.delenv("WECHAT_AGENT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)


def _create_topic(client: TestClient) -> str:
    task = client.post("/api/tasks/demo").json()
    topics = client.get(f"/api/tasks/{task['id']}/topics").json()["items"]
    return topics[0]["id"]


def test_creation_project_builds_evidence_gated_brief_and_draft(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'creation.db'}"
    app = create_app(database_url=database_url, fixture_path=ACCEPTANCE_FIXTURE)

    with TestClient(app) as client:
        topic_id = _create_topic(client)
        created = client.post(
            "/api/v1/creation/projects",
            json={"topicId": topic_id},
        )
        assert created.status_code == 201
        project = created.json()
        assert project["topicId"] == topic_id
        assert project["stage"] == "evidence_ready"
        assert project["brief"] is None

        blocked = client.post(
            f"/api/v1/creation/projects/{project['id']}/drafts",
            json={},
        )
        assert blocked.status_code == 409
        assert blocked.json()["code"] == "BRIEF_NOT_READY"

        brief_response = client.post(
            f"/api/v1/creation/projects/{project['id']}/brief",
        )
        assert brief_response.status_code == 200
        project = brief_response.json()
        brief = project["brief"]
        assert project["stage"] == "brief_ready"
        assert brief["readyForDraft"] is True
        assert brief["blockingReasons"] == []
        assert len(brief["sections"]) == 5
        assert any(section["claimIds"] for section in brief["sections"])

        draft_response = client.post(
            f"/api/v1/creation/projects/{project['id']}/drafts",
            json={},
        )
        assert draft_response.status_code == 201
        result = draft_response.json()
        assert result["project"]["currentDraftId"] == result["draft"]["id"]
        assert result["project"]["stage"] == "review_passed"
        assert result["draft"]["quality"]["can_export"] is True
        for section in brief["sections"]:
            assert f"## {section['heading']}" in result["draft"]["body"]
        loaded_draft = client.get(
            f"/api/v1/creation/drafts/{result['draft']['id']}"
        )
        assert loaded_draft.status_code == 200
        assert loaded_draft.json()["id"] == result["draft"]["id"]
        polish = client.post(
            f"/api/v1/creation/drafts/{result['draft']['id']}/polish",
            json={
                "version": result["draft"]["version"],
                "writingStyle": "轻松口语",
                "creationMode": "synthesized",
                "promptVariant": "aigc",
                "referenceFocus": "mixed",
                "referenceLevel": "medium",
                "customInstruction": "先给结论",
            },
        )
        assert polish.status_code == 503
        assert polish.json()["code"] == "AI_POLISH_UNAVAILABLE"
        assert client.get(
            f"/api/v1/creation/drafts/{result['draft']['id']}"
        ).json()["version"] == result["draft"]["version"]
        exported = client.get(
            f"/api/v1/creation/drafts/{result['draft']['id']}/export",
            params={"format": "markdown"},
        )
        assert exported.status_code == 409
        assert exported.json()["code"] == "HUMAN_APPROVAL_REQUIRED"
        approved = client.post(
            f"/api/v1/creation/drafts/{result['draft']['id']}/approve",
            json={
                "version": result["draft"]["version"],
                "confirmation": "我已核对事实、作者材料和表达边界",
            },
        )
        assert approved.status_code == 200
        exported = client.get(
            f"/api/v1/creation/drafts/{result['draft']['id']}/export",
            params={"format": "wechat_html"},
        )
        assert exported.status_code == 200
        assert exported.text.startswith("<section")
        assert "参考来源" in exported.text
        assert client.get(
            f"/api/v1/creation/projects/{project['id']}"
        ).json()["stage"] == "exported"


def test_creation_project_uses_optimistic_version_and_restores_after_restart(
    tmp_path,
):
    database_url = f"sqlite:///{tmp_path / 'creation-restore.db'}"
    app = create_app(database_url=database_url, fixture_path=ACCEPTANCE_FIXTURE)

    with TestClient(app) as client:
        topic_id = _create_topic(client)
        project = client.post(
            "/api/v1/creation/projects",
            json={"topic_id": topic_id},
        ).json()
        project = client.post(
            f"/api/v1/creation/projects/{project['id']}/brief"
        ).json()
        stale_payload = {
            "version": project["version"] - 1,
            "coreThesis": project["brief"]["coreThesis"],
            "audienceBenefit": project["brief"]["audienceBenefit"],
            "angle": project["brief"]["angle"],
            "differentiation": project["brief"]["differentiation"],
            "sections": [
                {
                    "id": section["id"],
                    "heading": section["heading"],
                    "intent": section["intent"],
                    "claimIds": section["claimIds"],
                }
                for section in project["brief"]["sections"]
            ],
            "requiredSourceUrls": project["brief"]["requiredSourceUrls"],
            "risks": project["brief"]["risks"],
            "authorInputs": project["brief"]["authorInputs"],
        }
        conflict = client.post(
            f"/api/v1/creation/projects/{project['id']}/brief",
            json=stale_payload,
        )
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "PROJECT_VERSION_CONFLICT"
        project_id = project["id"]

    restarted = create_app(
        database_url=database_url,
        fixture_path=ACCEPTANCE_FIXTURE,
    )
    with TestClient(restarted) as client:
        restored = client.get(
            f"/api/v1/creation/projects/{project_id}"
        )
        assert restored.status_code == 200
        assert restored.json()["stage"] == "brief_ready"
        assert restored.json()["brief"]["readyForDraft"] is True


def test_creation_project_does_not_bypass_unready_evidence(tmp_path):
    fixture_path = tmp_path / "insufficient.json"
    fixture_path.write_text(
        """[{"id":"only","title":"One-source AI update","source_name":"Only Source","source_url":"https://example.com/only","published_at":"2026-07-25T08:00:00Z","collected_at":"2026-07-25T09:00:00Z","summary":"Only Source supports 2 modes.","category":"tool","source_type":"secondary","source_type_verified":false}]""",
        encoding="utf-8",
    )
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'blocked.db'}",
        fixture_path=fixture_path,
    )
    with TestClient(app) as client:
        topic_id = _create_topic(client)
        project = client.post(
            "/api/v1/creation/projects",
            json={"topic_id": topic_id},
        ).json()
        project = client.post(
            f"/api/v1/creation/projects/{project['id']}/brief"
        ).json()
        assert project["stage"] == "research"
        assert project["brief"]["readyForDraft"] is False
        assert project["brief"]["blockingReasons"]
        blocked = client.post(
            f"/api/v1/creation/projects/{project['id']}/drafts",
            json={},
        )
        assert blocked.status_code == 409
        assert blocked.json()["code"] == "BRIEF_NOT_READY"


def test_complete_creation_workflow_persists_profile_versions_and_export_audit(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'complete-creation.db'}"
    app = create_app(database_url=database_url, fixture_path=ACCEPTANCE_FIXTURE)

    with TestClient(app) as client:
        profile = client.get("/api/v1/creation/profile").json()
        updated_profile = client.put(
            "/api/v1/creation/profile",
            json={
                "version": profile["version"],
                "accountName": profile["accountName"],
                "positioning": profile["positioning"],
                "targetAudience": profile["targetAudience"],
                "contentPillars": profile["contentPillars"],
                "excludedTopics": profile["excludedTopics"],
                "toneKeywords": profile["toneKeywords"],
                "targetCharacters": profile["targetCharacters"],
                "paragraphLimit": profile["paragraphLimit"],
                "titlePreferences": profile["titlePreferences"],
                "forbiddenPhrases": profile["forbiddenPhrases"],
                "acceptedSourceTiers": profile["acceptedSourceTiers"],
                "citationStyle": profile["citationStyle"],
                "riskTolerance": profile["riskTolerance"],
                "verifiedExperienceInventory": ["使用过该类产品的公开版本"],
                "fixedViewpoints": ["先核验一手来源，再讨论采用价值"],
            },
        )
        assert updated_profile.status_code == 200
        assert updated_profile.json()["version"] == 2

        topic_id = _create_topic(client)
        opportunities = client.get("/api/v1/creation/opportunities")
        assert opportunities.status_code == 200
        assert opportunities.json()["count"] <= 10
        project = client.post(
            "/api/v1/creation/projects", json={"topicId": topic_id}
        ).json()
        assert project["profileVersion"] == 2
        assert project["opportunity"]["creationScore"] > 0

        project = client.post(
            f"/api/v1/creation/projects/{project['id']}/author-inputs",
            json={
                "projectVersion": project["version"],
                "kind": "opinion",
                "content": "作者认为应优先验证落地成本，而不是只看发布热度。",
                "confirmed": True,
                "sourceUrls": [],
            },
        ).json()
        assert project["authorInputs"][0]["confirmed"] is True
        project = client.post(
            f"/api/v1/creation/projects/{project['id']}/brief"
        ).json()
        result = client.post(
            f"/api/v1/creation/projects/{project['id']}/drafts", json={}
        ).json()
        assert result["project"]["draftVersions"][0]["generationMethod"] in {"ai", "rule"}
        assert result["project"]["qualityReports"]
        assert "作者补充" in result["draft"]["body"]

        approval = client.post(
            f"/api/v1/creation/drafts/{result['draft']['id']}/approve",
            json={
                "version": result["draft"]["version"],
                "confirmation": "我已核对事实、作者材料和表达边界",
            },
        )
        assert approval.status_code == 200
        approved = approval.json()
        assert approved["humanApprovedDraftVersion"] == 2
        assert approved["draftVersions"][-1]["generationMethod"] == "finalize"

        export = client.get(
            f"/api/v1/creation/drafts/{result['draft']['id']}/export",
            params={"format": "wechat_html"},
        )
        assert export.status_code == 200
        assert 'style="' in export.text
        stored = client.get(f"/api/v1/creation/projects/{project['id']}").json()
        assert stored["stage"] == "exported"
        assert stored["exportRecords"][-1]["format"] == "wechat_html"
        project_id = project["id"]

    restarted = create_app(database_url=database_url, fixture_path=ACCEPTANCE_FIXTURE)
    with TestClient(restarted) as client:
        restored = client.get(f"/api/v1/creation/projects/{project_id}").json()
        assert restored["stage"] == "exported"
        assert restored["humanApprovedDraftVersion"] == 2
        assert len(restored["draftVersions"]) == 2
        assert restored["qualityReports"][-1]["humanConfirmed"] is True
        assert restored["exportRecords"][-1]["contentSha256"]


def test_generation_runtime_and_quality_replay_are_persisted(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'generation-audit.db'}"
    app = create_app(database_url=database_url, fixture_path=ACCEPTANCE_FIXTURE)

    with TestClient(app) as client:
        runtime = client.get("/api/v1/creation/runtime")
        assert runtime.status_code == 200
        assert runtime.json()["activeMode"] == "rule"
        assert runtime.json()["configured"] is False
        assert runtime.json()["verified"] is False
        assert runtime.json()["healthState"] == "unconfigured"
        assert runtime.json()["distributionProcessing"] == {
            "enabled": False,
            "available": False,
            "state": "unconfigured",
            "callsToday": 0,
            "dailyCallLimit": 0,
            "reason": "LLM_NOT_CONFIGURED",
            "openedAt": None,
        }
        assert {item["stage"] for item in runtime.json()["promptVersions"]} == {
            "probe", "outline", "section", "editor", "fallback"
        }

        probe = client.post("/api/v1/creation/runtime/probe")
        assert probe.status_code == 409
        assert probe.json()["code"] == "LLM_NOT_CONFIGURED"

        _create_topic(client)
        replay = client.post(
            "/api/v1/creation/replays",
            params={"mode": "current", "limit": 10},
        )
        assert replay.status_code == 201
        report = replay.json()
        assert report["actualMode"] == "rule"
        assert report["sampleCount"] > 0
        assert report["unsupportedNumberCount"] == 0
        assert report["fabricatedExperienceCount"] == 0

        listed = client.get("/api/v1/creation/replays").json()
        assert listed["items"][0]["id"] == report["id"]

    restarted = create_app(database_url=database_url, fixture_path=ACCEPTANCE_FIXTURE)
    with TestClient(restarted) as client:
        runtime = client.get("/api/v1/creation/runtime").json()
        assert runtime["latestReplay"]["id"] == report["id"]
