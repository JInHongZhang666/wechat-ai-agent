from datetime import UTC, datetime
import json
from pathlib import Path

from fastapi.testclient import TestClient

from wechat_agent.main import create_app


FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "articles.json"
NOW = datetime(2026, 8, 4, tzinfo=UTC)


def _production_fixture(tmp_path: Path) -> Path:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    for index, article in enumerate(payload, start=1):
        article["source_url"] = f"https://double-review.ai/articles/{index}"
    path = tmp_path / "double-review-articles.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _label(category: str) -> dict[str, object]:
    return {
        "relevant": True,
        "category": category,
        "selected": True,
        "primary_source_correct": True,
        "story_key": None,
        "creation_worthy": True,
        "evidence_sufficient": True,
        "annotation_method": "human",
        "data_license": "public-source-metadata-only",
        "annotator": "initial-reviewer",
        "annotated_at": NOW.isoformat(),
        "notes": "Initial human label.",
    }


def _review(
    reviewer: str,
    *,
    selected: bool,
    reason_code: str,
) -> dict[str, object]:
    return {
        "selected": selected,
        "reason_code": reason_code,
        "notes": "Independent human review.",
        "reviewer": reviewer,
        "reviewed_at": NOW.isoformat(),
        "annotation_method": "human",
    }


def _seed_completed_first_reviews(client: TestClient) -> list[dict[str, object]]:
    assert client.post("/api/tasks/demo").status_code == 201
    assert client.post("/api/ops/evaluation/candidates/refresh").status_code == 200
    candidates = client.get(
        "/api/ops/evaluation/candidates",
        params={"status": "all", "limit": 100},
    ).json()["items"]
    for item in candidates:
        article = item["candidate"]["article"]
        assert client.put(
            f"/api/ops/evaluation/annotations/{article['id']}",
            json=_label(article["category"]),
        ).status_code == 200
    assert client.post(
        "/api/ops/evaluation/reviews/selection-audit-v1/seed"
    ).status_code == 200
    review_items = client.get(
        "/api/ops/evaluation/reviews/selection-audit-v1/items",
        params={"status": "pending", "limit": 100},
    ).json()["items"]
    for item in review_items:
        article_id = item["candidate"]["article"]["id"]
        assert client.put(
            "/api/ops/evaluation/reviews/selection-audit-v1/items/"
            f"{article_id}",
            json=_review(
                "reviewer-a",
                selected=True,
                reason_code="wechat_value",
            ),
        ).status_code == 200
    return review_items


def test_double_review_api_claims_blind_reviewer_and_adjudicates_conflict(
    tmp_path,
):
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'double-review-api.db'}",
        fixture_path=_production_fixture(tmp_path),
        enable_default_live_collector=False,
    )
    with TestClient(app) as client:
        first_items = _seed_completed_first_reviews(client)
        claim = client.post(
            "/api/ops/evaluation/reviews/selection-audit-v1/"
            "second-reviewer/claim",
            json={"reviewer": "reviewer-b", "claimed_at": NOW.isoformat()},
        )
        assert claim.status_code == 200

        blind = client.get(
            "/api/ops/evaluation/reviews/selection-audit-v1/second-review/items",
            params={"status": "pending", "limit": 100},
        )
        assert blind.status_code == 200
        for forbidden in (
            "initial_label",
            "reviewer_a",
            "suggestion",
            "distribution_score",
            "strategy_version",
        ):
            assert forbidden not in blind.text
        items = blind.json()["items"]
        assert len(items) == len(first_items)
        conflict_id = items[-1]["article"]["id"]
        for item in items:
            article_id = item["article"]["id"]
            selected = article_id != conflict_id
            response = client.put(
                "/api/ops/evaluation/reviews/selection-audit-v1/"
                f"second-review/items/{article_id}",
                json=_review(
                    "reviewer-b",
                    selected=selected,
                    reason_code="wechat_value" if selected else "too_niche",
                ),
            )
            assert response.status_code == 200

        status = client.get(
            "/api/ops/evaluation/reviews/selection-audit-v1/"
            "double-review/status"
        ).json()
        assert status["lifecycle"] == "awaiting_adjudication"
        conflicts = client.get(
            "/api/ops/evaluation/reviews/selection-audit-v1/adjudication/items"
        ).json()
        assert conflicts["total"] == 1
        saved = client.put(
            "/api/ops/evaluation/reviews/selection-audit-v1/"
            f"adjudication/items/{conflict_id}",
            json={
                "selected": False,
                "reason_code": "too_niche",
                "notes": "Final human resolution.",
                "adjudicator": "reviewer-a",
                "adjudicated_at": NOW.isoformat(),
                "annotation_method": "human",
            },
        )
        assert saved.status_code == 200
        assert client.get(
            "/api/ops/evaluation/reviews/selection-audit-v1/"
            "double-review/status"
        ).json()["lifecycle"] == "complete"


def test_double_review_api_returns_stable_conflict_errors(tmp_path):
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'double-review-errors.db'}",
        fixture_path=_production_fixture(tmp_path),
        enable_default_live_collector=False,
    )
    with TestClient(app) as client:
        _seed_completed_first_reviews(client)
        collision = client.post(
            "/api/ops/evaluation/reviews/selection-audit-v1/"
            "second-reviewer/claim",
            json={"reviewer": " INITIAL-REVIEWER ", "claimed_at": NOW.isoformat()},
        )
        assert collision.status_code == 409
        assert collision.json()["error"]["code"] == "REVIEWER_NOT_INDEPENDENT"
        assert client.post(
            "/api/ops/evaluation/reviews/selection-audit-v1/"
            "second-reviewer/claim",
            json={"reviewer": "reviewer-b", "claimed_at": NOW.isoformat()},
        ).status_code == 200
        second_claim = client.post(
            "/api/ops/evaluation/reviews/selection-audit-v1/"
            "second-reviewer/claim",
            json={"reviewer": "reviewer-c", "claimed_at": NOW.isoformat()},
        )
        assert second_claim.status_code == 409
        assert (
            second_claim.json()["error"]["code"]
            == "SECOND_REVIEWER_ALREADY_CLAIMED"
        )
        items = client.get(
            "/api/ops/evaluation/reviews/selection-audit-v1/second-review/items",
            params={"status": "pending", "limit": 100},
        ).json()["items"]
        first_id = items[0]["article"]["id"]
        mismatch = client.put(
            "/api/ops/evaluation/reviews/selection-audit-v1/"
            f"second-review/items/{first_id}",
            json=_review(
                "reviewer-c", selected=True, reason_code="wechat_value"
            ),
        )
        assert mismatch.status_code == 409
        assert mismatch.json()["error"]["code"] == "REVIEWER_NOT_INDEPENDENT"
        payload = _review("reviewer-b", selected=True, reason_code="wechat_value")
        assert client.put(
            "/api/ops/evaluation/reviews/selection-audit-v1/"
            f"second-review/items/{first_id}",
            json=payload,
        ).status_code == 200
        duplicate = client.put(
            "/api/ops/evaluation/reviews/selection-audit-v1/"
            f"second-review/items/{first_id}",
            json=payload,
        )
        assert duplicate.status_code == 409
        assert (
            duplicate.json()["error"]["code"]
            == "REVIEW_SUBMISSION_ALREADY_EXISTS"
        )
        early = client.get(
            "/api/ops/evaluation/reviews/selection-audit-v1/adjudication/items"
        )
        assert early.status_code == 409
        assert early.json()["error"]["code"] == "SECOND_REVIEW_INCOMPLETE"
        for item in items[1:]:
            article_id = item["article"]["id"]
            assert client.put(
                "/api/ops/evaluation/reviews/selection-audit-v1/"
                f"second-review/items/{article_id}",
                json=payload,
            ).status_code == 200
        consensus = client.put(
            "/api/ops/evaluation/reviews/selection-audit-v1/"
            f"adjudication/items/{first_id}",
            json={
                "selected": True,
                "reason_code": "wechat_value",
                "notes": "No conflict exists.",
                "adjudicator": "reviewer-a",
                "adjudicated_at": NOW.isoformat(),
                "annotation_method": "human",
            },
        )
        assert consensus.status_code == 409
        assert consensus.json()["error"]["code"] == "ADJUDICATION_NOT_REQUIRED"
