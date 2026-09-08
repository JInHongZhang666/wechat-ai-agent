from threading import Event

from wechat_agent.services.scheduler import CollectionScheduler


def test_scheduler_can_run_immediately_and_keeps_result_history():
    called = Event()
    scheduler = CollectionScheduler(interval_minutes=30)

    def collect():
        called.set()
        return {
            "id": "task-1",
            "status": "review_ready",
            "collected_article_count": 3,
            "topic_ids": ["topic-1"],
        }

    try:
        scheduler.start(collect, run_immediately=True)
        assert called.wait(timeout=2)
        assert scheduler.latest_task is not None
        assert scheduler.latest_task["id"] == "task-1"
        assert scheduler.running is True
    finally:
        scheduler.stop()


def test_scheduler_does_not_create_history_when_no_source_is_due():
    called = Event()
    scheduler = CollectionScheduler(interval_minutes=30)

    def poll():
        called.set()
        return None

    try:
        scheduler.start(poll, run_immediately=True)
        assert called.wait(timeout=2)
        assert scheduler.latest_task is None
        assert scheduler.recent_tasks == []
    finally:
        scheduler.stop()


def test_scheduler_can_materialize_daily_report_immediately():
    called = Event()
    scheduler = CollectionScheduler(interval_minutes=30)

    def build_daily():
        called.set()
        return {"date": "2026-08-01", "revision": 2}

    try:
        scheduler.start_daily(build_daily, run_immediately=True)
        assert called.wait(timeout=2)
        assert scheduler.latest_daily_result == {
            "date": "2026-08-01",
            "revision": 2,
        }
        assert scheduler.running is True
    finally:
        scheduler.stop()
