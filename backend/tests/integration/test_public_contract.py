import json

from fastapi.testclient import TestClient

from scripts.generate_public_contract import (
    OPENAPI_PATH,
    TYPES_PATH,
    WORKBENCH_TYPES_PATH,
    _serialized_openapi,
    _typescript,
)
from wechat_agent.api.public_openapi import (
    PUBLIC_V1_OPERATIONS,
    build_public_v1_openapi,
)
from wechat_agent.api.public_schemas import (
    DailyIndexResponse,
    DailyReportResponse,
    FingerprintResponse,
    HotTopicsResponse,
    ItemsResponse,
    SelectedChangesResponse,
    SelectedSnapshotResponse,
)
from wechat_agent.main import create_app


def _app(tmp_path):
    fixture_path = tmp_path / "empty.json"
    fixture_path.write_text("[]", encoding="utf-8")
    return create_app(
        database_url=f"sqlite:///{tmp_path / 'contract.db'}",
        fixture_path=fixture_path,
        enable_default_live_collector=False,
    )


def test_public_openapi_contains_only_aihot_distribution_contract(tmp_path):
    app = _app(tmp_path)
    expected_schema = build_public_v1_openapi(app)

    with TestClient(app) as client:
        response = client.get("/openapi-v1.json")

    schema = response.json()
    assert response.status_code == 200
    assert schema == expected_schema
    assert set(schema["paths"]) == set(PUBLIC_V1_OPERATIONS)
    assert all(
        set(path_item) == {"get"}
        for path_item in schema["paths"].values()
    )
    assert "/api/v1/sources" not in schema["paths"]
    assert "/api/v1/tasks/live" not in schema["paths"]
    assert "TaskPayload" not in schema["components"]["schemas"]
    assert not any("/ops/evaluation" in path for path in schema["paths"])
    assert not any("story-shadow" in path for path in schema["paths"])
    problem_content = schema["paths"]["/api/v1/items"]["get"][
        "responses"
    ]["422"]["content"]
    assert set(problem_content) == {"application/problem+json"}


def test_committed_openapi_and_generated_types_do_not_drift(tmp_path):
    schema = build_public_v1_openapi(_app(tmp_path))
    committed_schema = json.loads(
        OPENAPI_PATH.read_text(encoding="utf-8")
    )
    generated_types = TYPES_PATH.read_text(encoding="utf-8")

    assert committed_schema == schema
    assert generated_types == _typescript(schema)
    assert WORKBENCH_TYPES_PATH.read_text(encoding="utf-8") == _typescript(
        schema
    )


def test_openapi_file_serialization_is_independent_of_insertion_order():
    first = {"paths": {"/b": {}, "/a": {}}, "info": {"title": "AI"}}
    second = {"info": {"title": "AI"}, "paths": {"/a": {}, "/b": {}}}

    assert _serialized_openapi(first) == _serialized_openapi(second)


def test_public_responses_problem_json_cache_and_latency_contract(tmp_path):
    app = _app(tmp_path)

    with TestClient(app) as client:
        first = client.get("/api/v1/items?limit=3")
        cached = client.get(
            "/api/v1/items?limit=3",
            headers={"If-None-Match": first.headers["etag"]},
        )
        hot_topics = client.get("/api/v1/hot-topics")
        daily = client.get("/api/v1/dailies/latest")
        daily_index = client.get("/api/v1/dailies")
        fingerprint = client.get("/api/v1/fingerprint")
        snapshot = client.get(
            "/api/v1/selected/snapshot?fields=minimal&limit=10"
        )
        changes = client.get(
            "/api/v1/selected/changes",
            params={"cursor": snapshot.json()["cursor"]},
        )
        invalid = client.get("/api/v1/items?q=x")
        missing = client.get("/api/v1/not-a-public-resource")
        observations = client.get(
            "/api/ops/http-observability?window_minutes=60"
        ).json()

    ItemsResponse.model_validate(first.json())
    HotTopicsResponse.model_validate(hot_topics.json())
    DailyReportResponse.model_validate(daily.json())
    DailyIndexResponse.model_validate(daily_index.json())
    FingerprintResponse.model_validate(fingerprint.json())
    SelectedSnapshotResponse.model_validate(snapshot.json())
    SelectedChangesResponse.model_validate(changes.json())
    assert first.headers["x-cache-status"] == "MISS"
    assert cached.status_code == 304
    assert cached.content == b""
    assert cached.headers["x-cache-status"] == "HIT"
    assert "dur=" in cached.headers["server-timing"]
    assert invalid.status_code == 422
    assert invalid.headers["content-type"].startswith(
        "application/problem+json"
    )
    assert invalid.json()["code"] == "INVALID_REQUEST"
    assert invalid.json()["requestId"] == invalid.headers["x-request-id"]
    assert missing.status_code == 404
    assert missing.headers["content-type"].startswith(
        "application/problem+json"
    )

    item_route = next(
        route
        for route in observations["routes"]
        if route["method"] == "GET" and route["route"] == "/api/v1/items"
    )
    assert observations["scope"] == "process_local"
    assert item_route["requestCount"] == 3
    assert item_route["cache"] == {
        "hits": 1,
        "misses": 1,
        "bypasses": 1,
        "hitRate": 0.5,
    }
    assert item_route["durationMs"]["p95"] < 200
