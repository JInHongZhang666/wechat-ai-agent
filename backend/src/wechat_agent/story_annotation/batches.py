"""Immutable Story annotation batch initialization."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re

from pydantic import ValidationError

from .artifacts import canonical_json_bytes, canonical_jsonl_bytes, sha256_bytes, write_new_artifact
from .models import BatchManifest, StoryAnnotationError, StoryPair


_FORBIDDEN = ("score", "suggestion", "veto", "cluster", "storyid", "systemanswer", "samplingstratum")


def _normalized_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _reject_private_fields(value, *, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = _normalized_key(key)
            if any(name in normalized for name in _FORBIDDEN):
                raise StoryAnnotationError(
                    "FORBIDDEN_CANDIDATE_FIELD",
                    "candidate contains an algorithm-private field",
                    {"path": f"{path}.{key}"},
                )
            _reject_private_fields(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_private_fields(child, path=f"{path}[{index}]")


def load_candidate_jsonl(path: Path) -> tuple[StoryPair, ...]:
    pairs: list[StoryPair] = []
    pair_ids: set[str] = set()
    unordered_pairs: set[tuple[str, str]] = set()
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise StoryAnnotationError("CANDIDATE_UNREADABLE", "candidate file cannot be read") from error
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            _reject_private_fields(raw)
            pair = StoryPair.model_validate(raw)
        except StoryAnnotationError:
            raise
        except (json.JSONDecodeError, ValidationError) as error:
            raise StoryAnnotationError(
                "INVALID_CANDIDATE", "candidate row is invalid", {"line": line_number}
            ) from error
        if pair.pair_id in pair_ids:
            raise StoryAnnotationError("DUPLICATE_PAIR_ID", "pairId must be unique")
        identity = tuple(sorted((pair.article_a.id, pair.article_b.id)))
        if identity in unordered_pairs:
            raise StoryAnnotationError("DUPLICATE_ARTICLE_PAIR", "unordered article pair must be unique")
        pair_ids.add(pair.pair_id)
        unordered_pairs.add(identity)
        pairs.append(pair)
    if not pairs:
        raise StoryAnnotationError("EMPTY_CANDIDATE", "candidate file contains no pairs")
    return tuple(sorted(pairs, key=lambda item: item.pair_id))


def _blank_label(batch_id: str, pair_id: str) -> dict:
    return {
        "schemaVersion": 1,
        "batchId": batch_id,
        "pairId": pair_id,
        "sameStory": None,
        "relationReason": None,
        "entityAnchors": {"organizations": [], "products": [], "versions": [], "people": []},
        "timeAnchor": None,
        "locationAnchor": None,
        "hardNegative": False,
        "primarySourceArticleId": None,
        "annotationMethod": "human",
        "dataLicense": "public-source-metadata-only",
        "annotator": None,
        "annotatedAt": None,
        "notes": "",
    }


def initialize_batch(
    candidate_path: Path,
    batch_dir: Path,
    *,
    batch_id: str,
    strategy_version: str,
    created_at: datetime | str,
) -> BatchManifest:
    candidate_path = Path(candidate_path)
    batch_dir = Path(batch_dir)
    pairs = load_candidate_jsonl(candidate_path)
    manifest = BatchManifest.model_validate(
        {
            "batchId": batch_id,
            "strategyVersion": strategy_version,
            "candidateSha256": sha256_bytes(candidate_path.read_bytes()),
            "pairCount": len(pairs),
            "createdAt": created_at,
            "pairs": pairs,
        }
    )
    paths = (
        batch_dir / "manifest.json",
        batch_dir / "reviewer-a.template.jsonl",
        batch_dir / "reviewer-b.template.jsonl",
    )
    existing = next((path for path in paths if path.exists()), None)
    if existing is not None:
        raise FileExistsError(existing)
    template = canonical_jsonl_bytes(_blank_label(batch_id, pair.pair_id) for pair in pairs)
    write_new_artifact(paths[0], canonical_json_bytes(manifest))
    write_new_artifact(paths[1], template)
    write_new_artifact(paths[2], template)
    return manifest
