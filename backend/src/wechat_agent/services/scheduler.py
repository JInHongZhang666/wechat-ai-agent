"""Lightweight poller for the persistent per-source scheduler.

The poll interval only asks the service for due sources. Source.nextRunAt,
database leases, backoff, and circuit state decide what actually executes.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from threading import Lock

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)


class CollectionScheduler:
    """Poll for due sources and keep a small diagnostic task history."""

    def __init__(
        self,
        *,
        interval_minutes: int = 1,
        max_history: int = 48,
    ) -> None:
        self._scheduler = BackgroundScheduler(daemon=True)
        self._interval = interval_minutes
        self._max_history = max_history
        self._tasks: list[dict] = []  # Simple rolling history
        self._lock = Lock()
        self._collection_fn = None
        self._daily_fn = None
        self._latest_daily_result: dict | None = None
        self._running = False

    def start(self, collection_fn, *, run_immediately: bool = False) -> None:
        """Start the scheduler with a collection function.

        The collection_fn should accept no arguments and return a task-like dict
        with keys: id, status, collected_article_count, topic_ids.
        """
        self._collection_fn = collection_fn
        if not self._scheduler.running:
            self._scheduler.start()
        self._scheduler.add_job(
            self._do_collect,
            IntervalTrigger(minutes=self._interval),
            id="live_collection",
            name="Live public source collection",
            next_run_time=(
                datetime.now(UTC)
                if run_immediately
                else datetime.now(UTC) + timedelta(minutes=self._interval)
            ),
            replace_existing=True,
        )
        self._running = True
        logger.info(
            "CollectionScheduler started (interval=%dmin, max_history=%d)",
            self._interval, self._max_history,
        )

    def start_daily(
        self,
        daily_fn,
        *,
        hour: int = 8,
        minute: int = 0,
        timezone: str = "Asia/Shanghai",
        run_immediately: bool = False,
    ) -> None:
        """Schedule one idempotent persisted report build per local day."""
        self._daily_fn = daily_fn
        if not self._scheduler.running:
            self._scheduler.start()
        immediate_options = (
            {"next_run_time": datetime.now(UTC)}
            if run_immediately
            else {}
        )
        self._scheduler.add_job(
            self._do_daily,
            CronTrigger(hour=hour, minute=minute, timezone=timezone),
            id="daily_report",
            name="Persist previous Asia/Shanghai daily report",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            **immediate_options,
        )
        self._running = True
        logger.info(
            "Daily report scheduler started (%02d:%02d %s)",
            hour,
            minute,
            timezone,
        )

    def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        self._running = False
        logger.info("CollectionScheduler stopped")

    def _do_collect(self) -> None:
        if self._collection_fn is None:
            return
        try:
            result = self._collection_fn()
            if result is None:
                logger.debug("Background source poll found no due sources")
                return
            with self._lock:
                self._tasks.append(result)
                if len(self._tasks) > self._max_history:
                    self._tasks = self._tasks[-self._max_history:]
            logger.info(
                "Background collection done: %d articles, status=%s",
                result.get("collected_article_count", 0),
                result.get("status", "?"),
            )
        except Exception as exc:
            logger.error("Background collection failed: %s", exc)

    def _do_daily(self) -> None:
        if self._daily_fn is None:
            return
        try:
            result = self._daily_fn()
            with self._lock:
                self._latest_daily_result = result
            logger.info(
                "Daily report persisted: date=%s revision=%s",
                result.get("date", "?"),
                result.get("revision", "?"),
            )
        except Exception:
            logger.exception("Daily report generation failed")

    @property
    def latest_task(self) -> dict | None:
        with self._lock:
            return self._tasks[-1] if self._tasks else None

    @property
    def recent_tasks(self) -> list[dict]:
        with self._lock:
            return list(self._tasks)

    @property
    def latest_daily_result(self) -> dict | None:
        with self._lock:
            return self._latest_daily_result

    @property
    def running(self) -> bool:
        return self._running


# ── Factory ──────────────────────────────────────────────────────────

def create_scheduler() -> CollectionScheduler:
    """Create a new scheduler instance. Caller is responsible for lifecycle."""
    interval = int(os.getenv("WECHAT_AGENT_SOURCE_POLL_MINUTES", "1"))
    return CollectionScheduler(interval_minutes=max(interval, 1))
