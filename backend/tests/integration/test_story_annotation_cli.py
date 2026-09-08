from __future__ import annotations

import json

from wechat_agent.story_annotation.__main__ import main


def candidate(article_a="a", article_b="b"):
    def article(value):
        return {
            "id": value, "title": value, "summary": "summary", "sourceName": "source",
            "sourceUrl": f"https://example.com/{value}", "publisherGroup": "publisher:test",
            "publishedAt": "2026-08-27T00:00:00Z", "category": "industry",
        }
    return {"pairId": "p1", "articleA": article(article_a), "articleB": article(article_b)}


def label(reviewer):
    return {
        "schemaVersion": 1, "batchId": "batch-1", "pairId": "p1", "sameStory": "yes",
        "relationReason": "official_and_coverage", "entityAnchors": {}, "timeAnchor": None,
        "locationAnchor": None, "hardNegative": False, "primarySourceArticleId": None,
        "annotationMethod": "human", "dataLicense": "public-source-metadata-only",
        "annotator": reviewer, "annotatedAt": "2026-08-27T00:00:00Z", "notes": "",
    }


def setup_batch(tmp_path):
    source = tmp_path / "pairs.jsonl"
    source.write_text(json.dumps(candidate()) + "\n", encoding="utf-8")
    batch = tmp_path / "batch"
    assert main([
        "init-batch", "--candidates", str(source), "--batch-dir", str(batch),
        "--batch-id", "batch-1", "--strategy-version", "story-shadow-v1",
        "--created-at", "2026-08-27T00:00:00Z",
    ]) == 0
    return batch


def test_cli_init_validate_and_lock_report(tmp_path, capsys):
    batch = setup_batch(tmp_path)
    capsys.readouterr()
    partial_b = tmp_path / "reviewer-b.partial.jsonl"
    partial_b.write_text("", encoding="utf-8")
    assert main([
        "validate-submission", "--manifest", str(batch / "manifest.json"),
        "--submission", str(partial_b), "--reviewer", "reviewer-b",
    ]) == 2
    error = json.loads(capsys.readouterr().err)
    assert error["code"] == "SUBMISSION_INCOMPLETE"
    assert "labels" not in error

    assert main([
        "agreement", "--manifest", str(batch / "manifest.json"),
        "--reviewer-a", str(tmp_path / "must-not-be-read.jsonl"),
        "--reviewer-b", str(partial_b), "--output", str(tmp_path / "agreement.json"),
        "--generated-at", "2026-08-27T00:00:00Z",
    ]) == 2
    error = json.loads(capsys.readouterr().err)
    assert error["code"] == "SECOND_REVIEW_INCOMPLETE"
    assert "pairId" not in json.dumps(error)
    assert not (tmp_path / "agreement.json").exists()


def test_cli_refuses_to_overwrite_agreement_evidence(tmp_path, capsys):
    batch = setup_batch(tmp_path)
    capsys.readouterr()
    paths = []
    for reviewer in ("reviewer-a", "reviewer-b"):
        path = tmp_path / f"{reviewer}.jsonl"
        path.write_text(json.dumps(label(reviewer)) + "\n", encoding="utf-8")
        paths.append(path)
    output = tmp_path / "agreement.json"
    output.write_text("original", encoding="utf-8")
    assert main([
        "agreement", "--manifest", str(batch / "manifest.json"),
        "--reviewer-a", str(paths[0]), "--reviewer-b", str(paths[1]),
        "--output", str(output), "--generated-at", "2026-08-27T00:00:00Z",
    ]) == 2
    assert output.read_text(encoding="utf-8") == "original"
    assert json.loads(capsys.readouterr().err)["code"] == "ARTIFACT_EXISTS"


def test_cli_runs_agreement_adjudication_and_gate_commands(tmp_path, capsys):
    batch = setup_batch(tmp_path)
    capsys.readouterr()
    reviewer_paths = []
    for reviewer in ("reviewer-a", "reviewer-b"):
        path = tmp_path / f"{reviewer}.jsonl"
        path.write_text(json.dumps(label(reviewer)) + "\n", encoding="utf-8")
        reviewer_paths.append(path)
    agreement = tmp_path / "agreement.json"
    assert main([
        "agreement", "--manifest", str(batch / "manifest.json"),
        "--reviewer-a", str(reviewer_paths[0]), "--reviewer-b", str(reviewer_paths[1]),
        "--output", str(agreement), "--generated-at", "2026-08-27T00:00:00Z",
    ]) == 0
    agreement_sha = json.loads(capsys.readouterr().out)["sha256"]
    empty = tmp_path / "adjudication.input.jsonl"
    empty.write_text("", encoding="utf-8")
    adjudication = tmp_path / "adjudication.validated.json"
    assert main([
        "validate-adjudication", "--manifest", str(batch / "manifest.json"),
        "--agreement", str(agreement), "--reviewer-a", str(reviewer_paths[0]),
        "--reviewer-b", str(reviewer_paths[1]), "--adjudication", str(empty),
        "--agreement-sha256", agreement_sha, "--output", str(adjudication),
    ]) == 0
    capsys.readouterr()
    gate = tmp_path / "gate.json"
    assert main([
        "release-gate", "--stage", "pilot-50", "--manifest", str(batch / "manifest.json"),
        "--agreement", str(agreement), "--adjudication", str(adjudication), "--output", str(gate),
    ]) == 2
    assert json.loads(gate.read_text(encoding="utf-8"))["publicCutoverEnabled"] is False
