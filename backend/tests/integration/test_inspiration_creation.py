from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from wechat_agent.domain.models import InspirationSource, InspirationStructure, SourceType, TopicCategory
from wechat_agent.main import create_app


FIXTURE = Path(__file__).parents[1] / "fixtures" / "acceptance_articles.json"


def _source(url: str, *, role: str, publisher: str, verified: bool = False) -> InspirationSource:
    return InspirationSource(
        id=f"source-{publisher}", role=role,
        source_kind="user_url" if role == "primary" else "supporting_url",
        url=url, title="Acceptance Atlas 原创研究", publisher=publisher,
        publisher_identity=f"publisher:{publisher}",
        content_text=(
            "这篇参考资料围绕产品能力、应用场景、采用成本与风险边界展开。"
            "文章提供公开事实，并提醒读者结合独立来源继续核验。"
        ) * 12,
        content_sha256=("a" if role == "primary" else "b") * 64,
        source_type=SourceType.OFFICIAL if verified else SourceType.SECONDARY,
        source_type_verified=verified,
        source_verification_basis="integration fixture" if verified else None,
        category=TopicCategory.TOOL,
        structure=InspirationStructure(
            paragraph_count=8,
            reusable_blueprint=["以事实切入", "按背景、事实、影响、判断重新组织"],
        ),
    )


def test_hot_article_creation_endpoint_discloses_score_not_read_count(tmp_path):
    app = create_app(database_url=f"sqlite:///{tmp_path / 'hot.db'}", fixture_path=FIXTURE)
    with TestClient(app) as client:
        client.post("/api/tasks/demo")
        response = client.get("/api/v1/creation/hot-articles?limit=5")
        assert response.status_code == 200
        payload = response.json()
        assert payload["metricLabel"] == "AIHOT 分发质量分"
        assert "不等同于微信阅读量" in payload["metricDisclosure"]
        assert isinstance(payload["items"], list)


def test_manual_text_preview_and_project_creation_share_the_same_source_contract(
    tmp_path, monkeypatch
):
    primary = _source("https://primary.example/article", role="primary", publisher="manual")
    supporting = _source(
        "https://official.example/facts", role="supporting", publisher="official", verified=True
    )

    def fake_manual(url, content_text, **kwargs):
        assert url == primary.url
        assert len(content_text) >= 120
        assert kwargs["title"] == "用户指定标题"
        return primary

    def fake_fetch(url, *, role, source_kind, known_article=None, client=None):
        assert role == "supporting"
        return supporting

    monkeypatch.setattr("wechat_agent.api.v1_routes.source_from_manual_text", fake_manual)
    monkeypatch.setattr("wechat_agent.api.v1_routes.fetch_inspiration_source", fake_fetch)
    app = create_app(database_url=f"sqlite:///{tmp_path / 'manual.db'}", fixture_path=FIXTURE)
    manual_text = "用户补充的参考正文只用于主题理解和结构分析，不会直接成为事实证据。" * 8

    with TestClient(app) as client:
        preview = client.post(
            "/api/v1/creation/inspiration/preview",
            json={
                "primaryUrl": primary.url,
                "primaryTitle": "用户指定标题",
                "primaryText": manual_text,
                "sourceKind": "user_url",
            },
        )
        assert preview.status_code == 200
        preview_payload = preview.json()
        assert preview_payload["source"]["url"] == primary.url
        assert "contentText" not in preview_payload["source"]
        assert "事实仍需独立来源确认" in preview_payload["policy"]

        created = client.post(
            "/api/v1/creation/projects/from-inspiration",
            json={
                "primaryUrl": primary.url,
                "primaryTitle": "用户指定标题",
                "primaryText": manual_text,
                "supportingUrls": [supporting.url],
                "sourceKind": "user_url",
            },
        )
        assert created.status_code == 201
        project = created.json()
        assert project["origin"] == "inspiration"
        assert project["inspirationSources"][0]["url"] == primary.url
        assert "contentText" not in project["inspirationSources"][0]


def test_inspiration_project_persists_sources_and_requires_ten_percent_gate(tmp_path, monkeypatch):
    primary = _source("https://primary.example/article", role="primary", publisher="primary")
    first = _source("https://official.example/facts", role="supporting", publisher="official", verified=True)
    second = _source("https://independent.example/report", role="supporting", publisher="independent")

    def fake_fetch(url, *, role, source_kind, known_article=None, client=None):
        return {primary.url: primary, first.url: first, second.url: second}[url]

    monkeypatch.setattr("wechat_agent.api.v1_routes.fetch_inspiration_source", fake_fetch)
    app = create_app(database_url=f"sqlite:///{tmp_path / 'inspiration.db'}", fixture_path=FIXTURE)
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/creation/projects/from-inspiration",
            json={
                "primaryUrl": primary.url,
                "supportingUrls": [first.url, second.url],
                "sourceKind": "user_url",
            },
        )
        assert created.status_code == 201
        project = created.json()
        assert project["origin"] == "inspiration"
        assert project["stage"] == "evidence_ready"
        assert len(project["inspirationSources"]) >= 3
        assert "contentText" not in project["inspirationSources"][0]

        planned = client.post(f"/api/v1/creation/projects/{project['id']}/brief")
        assert planned.status_code == 200
        result = client.post(f"/api/v1/creation/projects/{project['id']}/drafts", json={})
        assert result.status_code == 201
        payload = result.json()
        reports = payload["project"]["similarityReports"]
        assert reports
        assert reports[0]["threshold"] == 0.10
        issue_codes = {item["code"] for item in payload["draft"]["quality"]["issues"]}
        assert "inspiration_similarity_exceeded" not in issue_codes
        project_id = project["id"]

        copied = client.put(
            f"/api/v1/creation/drafts/{payload['draft']['id']}",
            json={
                "selectedTitle": payload["draft"]["selected_title"],
                "body": primary.content_text,
                "version": payload["draft"]["version"],
            },
        )
        assert copied.status_code == 200
        copied_payload = copied.json()
        assert any(
            item["code"] == "inspiration_similarity_exceeded"
            for item in copied_payload["quality"]["issues"]
        )
        approval = client.post(
            f"/api/v1/creation/drafts/{payload['draft']['id']}/approve",
            json={
                "version": copied_payload["version"],
                "confirmation": "我已核对事实、作者材料和表达边界",
            },
        )
        assert approval.status_code == 409

    restored_app = create_app(
        database_url=f"sqlite:///{tmp_path / 'inspiration.db'}", fixture_path=FIXTURE
    )
    with TestClient(restored_app) as client:
        restored = client.get(f"/api/v1/creation/projects/{project_id}").json()
        assert restored["origin"] == "inspiration"
        assert restored["similarityReports"]


def test_inspiration_endpoint_rejects_private_target(tmp_path, monkeypatch):
    def reject(*args, **kwargs):
        from wechat_agent.writing.inspiration import InspirationImportError
        raise InspirationImportError("PRIVATE_URL_BLOCKED", "不能导入本机或内网地址。")

    monkeypatch.setattr("wechat_agent.api.v1_routes.fetch_inspiration_source", reject)
    app = create_app(database_url=f"sqlite:///{tmp_path / 'private.db'}", fixture_path=FIXTURE)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/creation/projects/from-inspiration",
            json={"primaryUrl": "http://127.0.0.1/admin", "supportingUrls": []},
        )
        assert response.status_code == 422
        assert response.json()["code"] == "PRIVATE_URL_BLOCKED"
