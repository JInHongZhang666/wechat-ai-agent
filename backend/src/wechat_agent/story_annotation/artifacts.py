"""Canonical and immutable local artifact helpers."""

from __future__ import annotations

from collections.abc import Iterable
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from pydantic import BaseModel


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True)
    return value


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            _json_value(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def canonical_jsonl_bytes(values: Iterable[Any]) -> bytes:
    return b"".join(canonical_json_bytes(value) for value in values)


def sha256_bytes(body: bytes) -> str:
    return sha256(body).hexdigest()


def write_new_artifact(path: Path, body: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(path)
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(delete=False, dir=path.parent) as handle:
            temporary = Path(handle.name)
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            raise FileExistsError(path)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
