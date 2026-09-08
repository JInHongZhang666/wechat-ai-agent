"""Export public OpenAPI v1 and deterministic TypeScript response types."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT / "src"))

from wechat_agent.api.public_openapi import build_public_v1_openapi  # noqa: E402
from wechat_agent.main import create_app  # noqa: E402


OPENAPI_PATH = PROJECT_ROOT / "openapi-v1.json"
TYPES_PATH = (
    BACKEND_ROOT
    / "src"
    / "wechat_agent"
    / "web"
    / "src"
    / "types"
    / "api-v1.generated.ts"
)
WORKBENCH_TYPES_PATH = (
    PROJECT_ROOT / "frontend" / "src" / "types" / "api-v1.generated.ts"
)


def main() -> None:
    app = create_app(enable_default_live_collector=False)
    schema = build_public_v1_openapi(app)
    OPENAPI_PATH.write_text(
        _serialized_openapi(schema),
        encoding="utf-8",
    )
    typescript = _typescript(schema)
    TYPES_PATH.write_text(typescript, encoding="utf-8")
    WORKBENCH_TYPES_PATH.write_text(typescript, encoding="utf-8")
    print(f"wrote {OPENAPI_PATH}")
    print(f"wrote {TYPES_PATH}")
    print(f"wrote {WORKBENCH_TYPES_PATH}")


def _serialized_openapi(schema: dict[str, Any]) -> str:
    """Return canonical JSON so contract hashes do not depend on set order."""
    return json.dumps(
        schema,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def _typescript(schema: dict[str, Any]) -> str:
    schemas = schema.get("components", {}).get("schemas", {})
    lines = [
        "// Generated from openapi-v1.json. Do not edit by hand.",
        "// Run: python scripts/generate_public_contract.py",
        "",
    ]
    for name in sorted(schemas):
        definition = schemas[name]
        if definition.get("type") == "object" and "properties" in definition:
            lines.extend(_typescript_interface(name, definition))
        else:
            lines.append(f"export type {name} = {_typescript_type(definition)}")
        lines.append("")

    operations: list[tuple[str, str]] = []
    for path, path_item in schema.get("paths", {}).items():
        for method, operation in path_item.items():
            response_schema = (
                operation.get("responses", {})
                .get("200", {})
                .get("content", {})
                .get("application/json", {})
                .get("schema", {})
            )
            operations.append(
                (f"{method.upper()} {path}", _typescript_type(response_schema))
            )
    lines.append("export interface PublicV1Operations {")
    for operation, response_type in sorted(operations):
        lines.append(f"  {json.dumps(operation)}: {response_type}")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def _typescript_interface(name: str, schema: dict[str, Any]) -> list[str]:
    required = set(schema.get("required", []))
    lines = [f"export interface {name} {{"]
    for property_name, definition in schema.get("properties", {}).items():
        optional = "" if property_name in required else "?"
        lines.append(
            f"  {property_name}{optional}: {_typescript_type(definition)}"
        )
    if schema.get("additionalProperties"):
        lines.append("  [key: string]: unknown")
    lines.append("}")
    return lines


def _typescript_type(schema: Any) -> str:
    if not isinstance(schema, dict) or not schema:
        return "unknown"
    reference = schema.get("$ref")
    if isinstance(reference, str):
        return reference.rsplit("/", 1)[-1]
    if "const" in schema:
        return json.dumps(schema["const"], ensure_ascii=False)
    if "enum" in schema:
        return " | ".join(
            json.dumps(value, ensure_ascii=False) for value in schema["enum"]
        )
    alternatives = schema.get("anyOf") or schema.get("oneOf")
    if alternatives:
        return " | ".join(_typescript_type(item) for item in alternatives)
    schema_type = schema.get("type")
    if schema_type == "string":
        return "string"
    if schema_type in {"integer", "number"}:
        return "number"
    if schema_type == "boolean":
        return "boolean"
    if schema_type == "null":
        return "null"
    if schema_type == "array":
        return f"Array<{_typescript_type(schema.get('items', {}))}>"
    if schema_type == "object":
        additional = schema.get("additionalProperties")
        if isinstance(additional, dict):
            return f"Record<string, {_typescript_type(additional)}>"
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        fields = [
            f"{name}{'' if name in required else '?'}: {_typescript_type(value)}"
            for name, value in properties.items()
        ]
        return "{ " + "; ".join(fields) + " }"
    return "unknown"


if __name__ == "__main__":
    main()
