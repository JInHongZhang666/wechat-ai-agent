"""Build the AIHOT-aligned, distribution-only OpenAPI v1 document."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from fastapi import FastAPI


PUBLIC_V1_OPERATIONS: dict[str, set[str]] = {
    "/api/v1/items": {"get"},
    "/api/v1/hot-topics": {"get"},
    "/api/v1/dailies": {"get"},
    "/api/v1/dailies/latest": {"get"},
    "/api/v1/dailies/{date}": {"get"},
    "/api/v1/selected/snapshot": {"get"},
    "/api/v1/selected/changes": {"get"},
    "/api/v1/fingerprint": {"get"},
}


def build_public_v1_openapi(app: FastAPI) -> dict[str, Any]:
    """Return only stable anonymous read-only distribution operations."""
    full_schema = app.openapi()
    paths: dict[str, Any] = {}
    for path, methods in PUBLIC_V1_OPERATIONS.items():
        available = full_schema.get("paths", {}).get(path, {})
        selected = {
            method: deepcopy(available[method])
            for method in methods
            if method in available
        }
        if selected:
            paths[path] = selected

    for path_item in paths.values():
        for operation in path_item.values():
            for status_code in ("400", "404", "409", "422", "429", "503"):
                if status_code not in operation.get("responses", {}):
                    continue
                description = operation["responses"][status_code].get(
                    "description",
                    "Problem response",
                )
                operation["responses"][status_code] = {
                    "description": description,
                    "content": {
                        "application/problem+json": {
                            "schema": {
                                "$ref": "#/components/schemas/ProblemDetail"
                            }
                        }
                    },
                }

    schema = {
        "openapi": full_schema.get("openapi", "3.1.0"),
        "info": {
            "title": "AI 信息小屋 · 公开分发 API v1",
            "version": "1.0.0",
            "description": (
                "AIHOT-aligned anonymous read-only API for curated AI news, "
                "hot topics, daily reports, durable selected-set sync, "
                "and low-traffic polling."
            ),
        },
        "paths": paths,
        "components": {"schemas": {}},
    }
    referenced = _referenced_schema_names(paths)
    source_schemas = full_schema.get("components", {}).get("schemas", {})
    pending = list(referenced)
    while pending:
        name = pending.pop()
        if name in schema["components"]["schemas"] or name not in source_schemas:
            continue
        component = deepcopy(source_schemas[name])
        schema["components"]["schemas"][name] = component
        pending.extend(_referenced_schema_names(component))
    return schema


def _referenced_schema_names(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        reference = value.get("$ref")
        if isinstance(reference, str) and reference.startswith(
            "#/components/schemas/"
        ):
            found.add(reference.rsplit("/", 1)[-1])
        for nested in value.values():
            found.update(_referenced_schema_names(nested))
    elif isinstance(value, list):
        for nested in value:
            found.update(_referenced_schema_names(nested))
    return found
