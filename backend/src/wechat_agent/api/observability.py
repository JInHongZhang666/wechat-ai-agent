"""Small, local HTTP observability layer for the single-process deployment."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import logging
from math import ceil
from threading import Lock


LOGGER = logging.getLogger("wechat_agent.http")


@dataclass(frozen=True)
class HttpObservation:
    observed_at: datetime
    request_id: str
    method: str
    route: str
    status: int
    duration_ms: float
    cache_status: str
    result_count: int | None


class HttpObservationRegistry:
    """Keep a bounded process-local window and expose aggregate diagnostics."""

    def __init__(self, max_samples: int = 10_000) -> None:
        self._samples: deque[HttpObservation] = deque(maxlen=max_samples)
        self._lock = Lock()

    def record(self, observation: HttpObservation) -> None:
        with self._lock:
            self._samples.append(observation)
        LOGGER.info(
            json.dumps(
                {
                    "event": "http_request",
                    "observedAt": observation.observed_at.isoformat(),
                    "requestId": observation.request_id,
                    "method": observation.method,
                    "route": observation.route,
                    "status": observation.status,
                    "durationMs": observation.duration_ms,
                    "cacheStatus": observation.cache_status,
                    "resultCount": observation.result_count,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )

    def snapshot(self, window_minutes: int = 60) -> dict[str, object]:
        now = datetime.now(UTC)
        cutoff = now - timedelta(minutes=window_minutes)
        with self._lock:
            samples = [sample for sample in self._samples if sample.observed_at >= cutoff]
        return {
            "schemaVersion": 1,
            "scope": "process_local",
            "generatedAt": now.isoformat(),
            "windowMinutes": window_minutes,
            "summary": _aggregate(samples),
            "routes": [
                {
                    "method": method,
                    "route": route,
                    **_aggregate(
                        [
                            sample
                            for sample in samples
                            if sample.method == method and sample.route == route
                        ]
                    ),
                }
                for method, route in sorted(
                    {(sample.method, sample.route) for sample in samples}
                )
            ],
        }


def _aggregate(samples: list[HttpObservation]) -> dict[str, object]:
    durations = sorted(sample.duration_ms for sample in samples)
    hits = sum(sample.cache_status == "HIT" for sample in samples)
    misses = sum(sample.cache_status == "MISS" for sample in samples)
    cacheable = hits + misses
    return {
        "requestCount": len(samples),
        "errorCount": sum(sample.status >= 500 for sample in samples),
        "cache": {
            "hits": hits,
            "misses": misses,
            "bypasses": sum(
                sample.cache_status == "BYPASS" for sample in samples
            ),
            "hitRate": round(hits / cacheable, 4) if cacheable else None,
        },
        "durationMs": {
            "average": (
                round(sum(durations) / len(durations), 3)
                if durations
                else None
            ),
            "p50": _percentile(durations, 0.50),
            "p95": _percentile(durations, 0.95),
            "max": round(durations[-1], 3) if durations else None,
        },
        "resultCount": sum(
            sample.result_count or 0 for sample in samples
        ),
    }


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    index = max(0, ceil(len(values) * percentile) - 1)
    return round(values[index], 3)
