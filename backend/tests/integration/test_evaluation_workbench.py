from datetime import UTC, datetime
import json
from pathlib import Path

from fastapi.testclient import TestClient

from wechat_agent.evaluation.__main__ import main
from wechat_agent.main import create_app


FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "articles.json"


def _production_fixture(tmp_path: Path) -> Path:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    for index, article in enumerate(payload, start=1):
        article["source_url"] = f"https://fixture-source.ai/articles/{index}"
    path = tmp_path / "production-articles.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _human_label(category: str) -> dict[str, object]:
    return {
        "relevant": True,
        "category": category,
        "selected": True,
        "primary_source_correct": True,
        "story_key": "workbench-test-story",
        "creation_worthy": True,
        "evidence_sufficient": True,
        "annotation_method": "human",
        "data_license": "public-source-metadata-only",
        "annotator": "reviewer-a",
        "annotated_at": datetime(2026, 8, 3, tzinfo=UTC).isoformat(),
        "notes": "Reviewed against the linked source.",
    }


def _review_payload(*, selected: bool, reason_code: str) -> dict[str, object]:
    return {
        "selected": selected,
        "reason_code": reason_code,
        "notes": "Second-pass review against the linked source.",
        "reviewer": "reviewer-b",
        "reviewed_at": datetime(2026, 8, 3, 1, tzinfo=UTC).isoformat(),
        "annotation_method": "human",
    }


def test_workbench_freezes_candidates_saves_human_label_and_exports(tmp_path):
    database_path = tmp_path / "workbench.db"
    database_url = f"sqlite:///{database_path}"
    app = create_app(
        database_url=database_url,
        fixture_path=_production_fixture(tmp_path),
        enable_default_live_collector=False,
    )
    with TestClient(app) as client:
        assert client.post("/api/tasks/demo").status_code == 201
        refreshed = client.post("/api/ops/evaluation/candidates/refresh")
        assert refreshed.status_code == 200
        status_before = refreshed.json()
        assert status_before["candidate_count"] > 0
        assert status_before["human_label_count"] == 0

        page = client.get(
            "/api/ops/evaluation/candidates",
            params={"status": "unlabelled", "limit": 1},
        )
        assert page.status_code == 200
        item = page.json()["items"][0]
        article = item["candidate"]["article"]
        label = _human_label(article["category"])

        saved = client.put(
            f"/api/ops/evaluation/annotations/{article['id']}",
            json=label,
        )
        assert saved.status_code == 200
        assert saved.json()["label"]["annotator"] == "reviewer-a"

        status_after = client.get("/api/ops/evaluation/status").json()
        assert status_after["human_label_count"] == 1
        assert status_after["remaining_count"] == 199

        gold = client.get("/api/ops/evaluation/gold.jsonl")
        assert gold.status_code == 200
        assert gold.headers["content-type"].startswith("application/x-ndjson")
        exported = json.loads(gold.text)
        assert exported["article"]["id"] == article["id"]
        assert exported["label"]["annotation_method"] == "human"

    output = tmp_path / "gold.jsonl"
    assert main([
        "export-gold",
        "--database-url",
        database_url,
        "--output",
        str(output),
    ]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["article"]["id"] == article["id"]


def test_workbench_rejects_synthetic_labels_and_unknown_candidates(tmp_path):
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'invalid.db'}",
        fixture_path=_production_fixture(tmp_path),
        enable_default_live_collector=False,
    )
    with TestClient(app) as client:
        label = _human_label("ai-models")
        label["annotation_method"] = "synthetic"
        synthetic = client.put(
            "/api/ops/evaluation/annotations/missing",
            json=label,
        )
        assert synthetic.status_code == 422
        assert synthetic.json()["error"]["code"] == "INVALID_EVALUATION_LABEL"

        label["annotation_method"] = "human"
        missing = client.put(
            "/api/ops/evaluation/annotations/missing",
            json=label,
        )
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "EVALUATION_CANDIDATE_NOT_FOUND"


def test_workbench_stable_refresh_does_not_replace_existing_pool(tmp_path):
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'stable.db'}",
        fixture_path=_production_fixture(tmp_path),
        enable_default_live_collector=False,
    )
    with TestClient(app) as client:
        assert client.post("/api/tasks/demo").status_code == 201
        first = client.post("/api/ops/evaluation/candidates/refresh").json()
        first_page = client.get(
            "/api/ops/evaluation/candidates",
            params={"status": "all", "limit": 100},
        ).json()
        second = client.post("/api/ops/evaluation/candidates/refresh").json()
        second_page = client.get(
            "/api/ops/evaluation/candidates",
            params={"status": "all", "limit": 100},
        ).json()

        assert second["candidate_count"] == first["candidate_count"]
        assert [item["candidate"]["article"]["id"] for item in second_page["items"]] == [
            item["candidate"]["article"]["id"] for item in first_page["items"]
        ]


def test_selection_review_api_seeds_and_adjudicates(tmp_path):
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'selection-review.db'}",
        fixture_path=_production_fixture(tmp_path),
        enable_default_live_collector=False,
    )
    with TestClient(app) as client:
        assert client.post("/api/tasks/demo").status_code == 201
        assert client.post(
            "/api/ops/evaluation/candidates/refresh"
        ).status_code == 200
        page = client.get(
            "/api/ops/evaluation/candidates",
            params={"status": "all", "limit": 100},
        ).json()
        for item in page["items"]:
            article = item["candidate"]["article"]
            response = client.put(
                f"/api/ops/evaluation/annotations/{article['id']}",
                json=_human_label(article["category"]),
            )
            assert response.status_code == 200

        seeded = client.post(
            "/api/ops/evaluation/reviews/selection-audit-v1/seed"
        )
        assert seeded.status_code == 200
        assert seeded.json()["total_count"] == len(page["items"])

        pending = client.get(
            "/api/ops/evaluation/reviews/selection-audit-v1/items",
            params={"status": "pending", "limit": 100},
        )
        assert pending.status_code == 200
        first = pending.json()["items"][0]
        article_id = first["candidate"]["article"]["id"]
        saved = client.put(
            "/api/ops/evaluation/reviews/selection-audit-v1/items/"
            f"{article_id}",
            json=_review_payload(
                selected=False,
                reason_code="low_information_gain",
            ),
        )
        assert saved.status_code == 200
        assert saved.json()["initial_label"]["selected"] is True
        assert saved.json()["review"]["selected"] is False

        duplicate = client.put(
            "/api/ops/evaluation/reviews/selection-audit-v1/items/"
            f"{article_id}",
            json=_review_payload(selected=True, reason_code="wechat_value"),
        )
        assert duplicate.status_code == 409
        assert (
            duplicate.json()["error"]["code"]
            == "EVALUATION_REVIEW_ALREADY_COMPLETED"
        )

        missing = client.put(
            "/api/ops/evaluation/reviews/selection-audit-v1/items/not-in-set",
            json=_review_payload(selected=True, reason_code="wechat_value"),
        )
        assert missing.status_code == 404
        assert (
            missing.json()["error"]["code"]
            == "EVALUATION_REVIEW_ITEM_NOT_FOUND"
        )

        status = client.get(
            "/api/ops/evaluation/reviews/selection-audit-v1/status"
        ).json()
        assert status["completed_count"] == 1
        assert status["changed_count"] == 1
        gold = client.get("/api/ops/evaluation/gold.jsonl")
        assert (
            gold.headers["content-disposition"]
            == 'attachment; filename="news-gold-current.jsonl"'
        )


def test_selection_review_status_before_seed_returns_stable_error(tmp_path):
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'missing-review-set.db'}",
        fixture_path=_production_fixture(tmp_path),
        enable_default_live_collector=False,
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            "/api/ops/evaluation/reviews/selection-audit-v1/status"
        )

    assert response.status_code == 404
    assert (
        response.json()["error"]["code"]
        == "EVALUATION_REVIEW_SET_NOT_FOUND"
    )
