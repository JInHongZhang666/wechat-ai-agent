"""Independent reviewer submission validation."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from .artifacts import sha256_bytes
from .models import (
    BatchManifest,
    StoryAnnotationError,
    StoryAnnotationLabel,
    ValidatedSubmission,
)


def validate_submission(
    manifest: BatchManifest,
    submission_path: Path,
    reviewer: str,
) -> ValidatedSubmission:
    try:
        body = Path(submission_path).read_bytes()
    except OSError as error:
        raise StoryAnnotationError("SUBMISSION_UNREADABLE", "submission cannot be read") from error

    pairs = {pair.pair_id: pair for pair in manifest.pairs}
    labels: dict[str, StoryAnnotationLabel] = {}
    for line_number, raw_line in enumerate(body.decode("utf-8").splitlines(), 1):
        if not raw_line.strip():
            continue
        try:
            label = StoryAnnotationLabel.model_validate(json.loads(raw_line))
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
            raise StoryAnnotationError(
                "INVALID_SUBMISSION_ROW", "submission row is invalid", {"line": line_number}
            ) from error
        if label.batch_id != manifest.batch_id:
            raise StoryAnnotationError("WRONG_BATCH", "label batchId does not match manifest")
        if label.annotator != reviewer:
            raise StoryAnnotationError("WRONG_REVIEWER", "label annotator does not match reviewer")
        if label.pair_id not in pairs:
            raise StoryAnnotationError("UNKNOWN_PAIR_ID", "label pairId is not in manifest")
        if label.pair_id in labels:
            raise StoryAnnotationError("DUPLICATE_PAIR_LABEL", "pairId appears more than once")
        pair = pairs[label.pair_id]
        if label.primary_source_article_id is not None and label.primary_source_article_id not in {
            pair.article_a.id,
            pair.article_b.id,
        }:
            raise StoryAnnotationError(
                "PRIMARY_SOURCE_OUTSIDE_PAIR",
                "primary source must identify one article in the pair",
            )
        labels[label.pair_id] = label

    missing = tuple(sorted(set(pairs) - set(labels)))
    return ValidatedSubmission(
        reviewer=reviewer,
        sha256=sha256_bytes(body),
        completed_count=len(labels),
        missing_pair_ids=missing,
        labels=labels,
    )
