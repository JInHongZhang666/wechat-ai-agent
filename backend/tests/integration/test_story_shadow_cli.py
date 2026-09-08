import json
from hashlib import sha256

from tests.story_factories import NOW, make_candidate_pair, make_story_article
from wechat_agent.storage.sqlite import SqliteArticleRepository
from wechat_agent.storage.story_sqlite import SqliteStoryRepository
from wechat_agent.story.__main__ import main
from wechat_agent.story.models import (
    PairSuggestion,
    StoryPersistenceBatch,
    StoryShadowRun,
)


def test_cli_replay_is_bounded(tmp_path, capsys):
    database_path = tmp_path / "story-cli.db"

    code = main(
        [
            "replay",
            "--database-url",
            f"sqlite:///{database_path}",
            "--days",
            "7",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["strategyVersion"] == "story-shadow-v1"
    assert payload["mode"] == "replay"
    assert payload["status"] == "success"
    assert database_path.exists()


def test_cli_rejects_more_than_seven_days_before_writing_state(tmp_path, capsys):
    database_path = tmp_path / "must-not-exist.db"

    code = main(
        [
            "replay",
            "--database-url",
            f"sqlite:///{database_path}",
            "--days",
            "8",
        ]
    )

    assert code == 2
    assert "between 1 and 7" in capsys.readouterr().err
    assert not database_path.exists()


def _seed_export_pair(database_url: str):
    article_repository = SqliteArticleRepository(database_url)
    left = make_story_article(
        id="export-left",
        source_id="publisher:left",
        source_name="Left Publisher",
        summary="Left evidence.",
    )
    right = make_story_article(
        id="export-right",
        source_id="publisher:right",
        source_name="Right Publisher",
        summary="Right evidence.",
    )
    article_repository.save_many((left, right))
    pair = make_candidate_pair(
        left,
        right,
        score=0.55,
        suggestion=PairSuggestion.AMBIGUOUS,
    )
    run = StoryShadowRun(
        id="story-run-export",
        mode="replay",
        status="success",
        window_start=NOW,
        window_end=NOW,
        started_at=NOW,
        finished_at=NOW,
    )
    SqliteStoryRepository(article_repository.engine).apply_batch(
        StoryPersistenceBatch(pairs=(pair,), run=run)
    )


def test_cli_exports_suggestion_blind_jsonl_and_exact_sha256(tmp_path, capsys):
    database_url = f"sqlite:///{tmp_path / 'export.db'}"
    output_path = tmp_path / "story-pairs.jsonl"
    _seed_export_pair(database_url)

    code = main(
        [
            "export-pairs",
            "--database-url",
            database_url,
            "--output",
            str(output_path),
            "--limit",
            "50",
        ]
    )

    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert summary["exportedCount"] == 1
    assert summary["sha256"] == sha256(output_path.read_bytes()).hexdigest()
    assert {"score", "suggestion", "vetoReason", "samplingStratum"}.isdisjoint(
        payload
    )


def test_cli_refuses_existing_export_without_force(tmp_path, capsys):
    database_url = f"sqlite:///{tmp_path / 'existing.db'}"
    output_path = tmp_path / "existing.jsonl"
    output_path.write_text("keep-me", encoding="utf-8")

    code = main(
        [
            "export-pairs",
            "--database-url",
            database_url,
            "--output",
            str(output_path),
            "--limit",
            "50",
        ]
    )

    assert code == 2
    assert "already exists" in capsys.readouterr().err
    assert output_path.read_text(encoding="utf-8") == "keep-me"
    assert not (tmp_path / "existing.db").exists()


def test_cli_writes_shadow_audit_report_without_public_cutover(tmp_path, capsys):
    database_url = f"sqlite:///{tmp_path / 'audit.db'}"
    output_path = tmp_path / "audit.json"
    _seed_export_pair(database_url)

    code = main(
        [
            "audit",
            "--database-url",
            database_url,
            "--output",
            str(output_path),
            "--generated-at",
            NOW.isoformat(),
        ]
    )

    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert summary["sha256"] == sha256(output_path.read_bytes()).hexdigest()
    assert report["pairCount"] == 1
    assert report["publicCutoverEnabled"] is False
