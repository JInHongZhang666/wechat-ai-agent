"""Truthful R1 release-gate evidence assembled from durable local data."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from wechat_agent.domain.ports import SourceRepository, SourceRunRepository
from wechat_agent.evaluation.io import read_gold
from wechat_agent.evaluation.replay import replay_samples
from wechat_agent.services.reliability import build_source_reliability_report


def build_r1_evidence_report(
    source_repository: SourceRepository,
    source_run_repository: SourceRunRepository,
    gold_path: Path,
    *,
    days: int = 7,
    required_gold: int = 200,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate the two R1 gates without manufacturing elapsed time or labels."""
    if required_gold < 1:
        raise ValueError("required_gold must be positive")
    generated_at = (now or datetime.now(UTC)).astimezone(UTC)
    reliability = build_source_reliability_report(
        source_repository,
        source_run_repository,
        days=days,
        now=generated_at,
    )
    blockers: list[dict[str, Any]] = []

    if not reliability["windowComplete"]:
        blockers.append(
            {
                "code": "RELIABILITY_WINDOW_INCOMPLETE",
                "detail": f"Not every enabled source has {days} days of run evidence.",
                "incompleteSourceIds": [
                    item["sourceId"]
                    for item in reliability["items"]
                    if not item["windowComplete"]
                ],
            }
        )
    operational_blockers: list[dict[str, Any]] = []
    stale_source_ids = [
        item["sourceId"]
        for item in reliability["items"]
        if not item["fresh"]
    ]
    if stale_source_ids:
        operational_blockers.append(
            {
                "code": "SOURCE_RUN_STALE",
                "detail": (
                    "One or more enabled sources have not attempted a run "
                    "within their schedule-aware freshness deadline."
                ),
                "sourceIds": stale_source_ids,
            }
        )
    interrupted_source_ids = [
        item["sourceId"]
        for item in reliability["items"]
        if not item["continuous"]
    ]
    if interrupted_source_ids:
        operational_blockers.append(
            {
                "code": "SOURCE_RUN_GAP",
                "detail": (
                    "One or more enabled sources have a silent run gap "
                    "longer than the maximum scheduler backoff and grace."
                ),
                "sourceIds": interrupted_source_ids,
            }
        )
    failing_source_ids = [
        item["sourceId"]
        for item in reliability["items"]
        if not item["meetsTarget"]
    ]
    if failing_source_ids:
        blockers.append(
            {
                "code": "SOURCE_TARGET_NOT_MET",
                "detail": "One or more enabled sources are below their tier target or have no samples.",
                "sourceIds": failing_source_ids,
            }
        )

    gold: dict[str, Any] = {
        "status": "missing",
        "path": str(gold_path),
        "requiredHumanSampleCount": required_gold,
        "totalSampleCount": 0,
        "humanSampleCount": 0,
        "syntheticSampleCount": 0,
        "datasetSha256": None,
        "baseline": None,
    }
    if not gold_path.is_file():
        blockers.append(
            {
                "code": "GOLD_FILE_MISSING",
                "detail": f"Human gold file does not exist: {gold_path}",
            }
        )
    else:
        try:
            raw = gold_path.read_bytes()
            samples = list(read_gold(gold_path))
        except (OSError, ValueError) as error:
            gold["status"] = "invalid"
            blockers.append(
                {
                    "code": "GOLD_INVALID",
                    "detail": str(error),
                }
            )
        else:
            gold["datasetSha256"] = sha256(raw).hexdigest()
            human_samples = [
                sample
                for sample in samples
                if sample.label.annotation_method == "human"
            ]
            gold.update(
                {
                    "totalSampleCount": len(samples),
                    "humanSampleCount": len(human_samples),
                    "syntheticSampleCount": len(samples) - len(human_samples),
                }
            )
            if len(human_samples) < required_gold:
                gold["status"] = "insufficient"
                blockers.append(
                    {
                        "code": "GOLD_SAMPLE_INSUFFICIENT",
                        "detail": (
                            f"Found {len(human_samples)} human labels; "
                            f"{required_gold} are required."
                        ),
                        "missingCount": required_gold - len(human_samples),
                    }
                )
            else:
                try:
                    gold["baseline"] = replay_samples(human_samples)
                except ValueError as error:
                    gold["status"] = "invalid"
                    blockers.append(
                        {
                            "code": "GOLD_REPLAY_INVALID",
                            "detail": str(error),
                        }
                    )
                else:
                    gold["status"] = "passing"

    passing = not blockers
    return {
        "schemaVersion": 1,
        "gate": "R1_EVIDENCE",
        "generatedAt": generated_at.isoformat(),
        "status": "passing" if passing else "blocked",
        "passing": passing,
        "criteria": {
            "sourceReliability": {
                "status": "passing" if reliability["passing"] else "blocked",
                "report": reliability,
            },
            "operationalReadiness": {
                "status": (
                    "passing"
                    if not operational_blockers
                    else "blocked"
                ),
                "passing": not operational_blockers,
                "blockers": operational_blockers,
            },
            "humanGold": gold,
        },
        "blockers": blockers,
    }
