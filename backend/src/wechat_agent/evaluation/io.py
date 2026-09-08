"""Strict readers for versioned evaluation datasets."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from pydantic import ValidationError

from wechat_agent.evaluation.models import GoldSample


def read_gold(path: Path) -> Iterator[GoldSample]:
    """Yield complete gold rows and preserve the failing JSONL line number."""
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not raw_line.strip():
            continue
        try:
            yield GoldSample.model_validate_json(raw_line)
        except ValidationError as error:
            raise ValueError(
                f"invalid gold sample at line {line_number}: {error}"
            ) from error
