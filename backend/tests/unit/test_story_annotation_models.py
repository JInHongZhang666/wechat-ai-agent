from __future__ import annotations

from hashlib import sha256
import json

import pytest
from pydantic import ValidationError

from wechat_agent.story_annotation import (
    EntityAnchors,
    StoryAnnotationLabel,
    canonical_json_bytes,
    canonical_jsonl_bytes,
    write_new_artifact,
)
from wechat_agent.story_annotation.batches import initialize_batch, load_candidate_jsonl
from wechat_agent.story_annotation.models import StoryAnnotationError
from wechat_agent.story_annotation.submissions import validate_submission


def valid_label(**overrides):
    payload = {
        "schemaVersion": 1,
        "batchId": "batch-50",
        "pairId": "pair-1",
        "sameStory": "yes",
        "relationReason": "official_and_coverage",
        "entityAnchors": {"organizations": ["OpenAI"]},
        "hardNegative": False,
        "primarySourceArticleId": None,
        "annotationMethod": "human",
        "dataLicense": "public-source-metadata-only",
        "annotator": "reviewer-a",
        "annotatedAt": "2026-08-27T00:00:00Z",
        "notes": "",
    }
    payload.update(overrides)
    return payload


def test_uncertain_label_requires_notes_and_fixed_fields():
    with pytest.raises(ValidationError):
        StoryAnnotationLabel.model_validate(
            valid_label(
                sameStory="uncertain",
                relationReason="insufficient_evidence",
                entityAnchors={},
                primarySourceArticleId=None,
                notes="",
            )
        )


def test_label_rejects_naive_timestamp_and_extra_fields():
    with pytest.raises(ValidationError):
        StoryAnnotationLabel.model_validate(
            valid_label(annotatedAt="2026-08-27T00:00:00", systemAnswer="same")
        )


def test_entity_anchors_are_deduplicated_and_sorted():
    anchors = EntityAnchors(organizations=["OpenAI", "Anthropic", "OpenAI"])
    assert anchors.organizations == ("Anthropic", "OpenAI")


def test_canonical_json_is_order_independent():
    assert canonical_json_bytes({"b": 2, "a": 1}) == canonical_json_bytes(
        {"a": 1, "b": 2}
    )
    assert canonical_jsonl_bytes([{"b": 2, "a": 1}]) == b'{"a":1,"b":2}\n'


def test_write_new_artifact_refuses_overwrite(tmp_path):
    path = tmp_path / "nested" / "artifact.json"
    write_new_artifact(path, b"first\n")
    with pytest.raises(FileExistsError):
        write_new_artifact(path, b"second\n")
    assert path.read_bytes() == b"first\n"
    assert sha256(path.read_bytes()).hexdigest() == sha256(b"first\n").hexdigest()


def test_canonical_model_output_uses_camel_case():
    label = StoryAnnotationLabel.model_validate(valid_label())
    value = json.loads(canonical_json_bytes(label))
    assert value["sameStory"] == "yes"
    assert "same_story" not in value


def candidate_row(pair_id="p1", article_a="a", article_b="b"):
    def article(article_id):
        return {
            "id": article_id,
            "title": f"Title {article_id}",
            "summary": "Summary",
            "sourceName": "Source",
            "sourceUrl": f"https://example.com/{article_id}",
            "publisherGroup": "publisher:example",
            "publishedAt": "2026-08-27T00:00:00Z",
            "category": "industry",
        }

    return {"pairId": pair_id, "articleA": article(article_a), "articleB": article(article_b)}


def write_candidate_rows(path, rows):
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_initialize_batch_hash_binds_manifest_and_templates(tmp_path):
    candidate = tmp_path / "pairs.jsonl"
    write_candidate_rows(candidate, [candidate_row("p2", "c", "d"), candidate_row()])
    manifest = initialize_batch(
        candidate,
        tmp_path / "batch",
        batch_id="story-pairs-shadow-v1-50",
        strategy_version="story-shadow-v1",
        created_at="2026-08-27T00:00:00Z",
    )
    assert manifest.pair_count == 2
    assert manifest.candidate_sha256 == sha256(candidate.read_bytes()).hexdigest()
    template_a = tmp_path / "batch/reviewer-a.template.jsonl"
    template_b = tmp_path / "batch/reviewer-b.template.jsonl"
    rows = [json.loads(line) for line in template_a.read_text(encoding="utf-8").splitlines()]
    assert [row["pairId"] for row in rows] == ["p1", "p2"]
    assert set(rows[0]) == {
        "annotatedAt", "annotationMethod", "annotator", "batchId", "dataLicense",
        "entityAnchors", "hardNegative", "locationAnchor", "notes", "pairId",
        "primarySourceArticleId", "relationReason", "sameStory", "schemaVersion",
        "timeAnchor",
    }
    assert template_a.read_bytes() == template_b.read_bytes()


@pytest.mark.parametrize("forbidden", ["score", "suggestion", "vetoReason", "storyId", "samplingStratum"])
def test_candidate_loader_rejects_algorithm_fields(tmp_path, forbidden):
    path = tmp_path / "leaky.jsonl"
    row = candidate_row()
    row[forbidden] = "private"
    write_candidate_rows(path, [row])
    with pytest.raises(StoryAnnotationError) as error:
        load_candidate_jsonl(path)
    assert error.value.code == "FORBIDDEN_CANDIDATE_FIELD"


def test_candidate_loader_rejects_duplicate_unordered_pair(tmp_path):
    path = tmp_path / "duplicate.jsonl"
    write_candidate_rows(path, [candidate_row("p1", "a", "b"), candidate_row("p2", "b", "a")])
    with pytest.raises(StoryAnnotationError) as error:
        load_candidate_jsonl(path)
    assert error.value.code == "DUPLICATE_ARTICLE_PAIR"


def make_manifest(tmp_path):
    candidate = tmp_path / "pairs.jsonl"
    write_candidate_rows(candidate, [candidate_row("p1", "a", "b"), candidate_row("p2", "c", "d")])
    return initialize_batch(
        candidate,
        tmp_path / "batch",
        batch_id="batch-50",
        strategy_version="story-shadow-v1",
        created_at="2026-08-27T00:00:00Z",
    )


def write_labels(path, labels):
    path.write_text(
        "".join(json.dumps(label, ensure_ascii=False) + "\n" for label in labels),
        encoding="utf-8",
    )


def test_validate_submission_reports_missing_pairs(tmp_path):
    manifest = make_manifest(tmp_path)
    path = tmp_path / "reviewer-b.jsonl"
    write_labels(path, [valid_label(batchId="batch-50", pairId="p1", annotator="reviewer-b")])
    result = validate_submission(manifest, path, "reviewer-b")
    assert result.completed_count == 1
    assert result.complete is False
    assert result.missing_pair_ids == ("p2",)
    assert len(result.sha256) == 64


def test_validate_submission_rejects_duplicate_label(tmp_path):
    manifest = make_manifest(tmp_path)
    path = tmp_path / "reviewer-b.jsonl"
    label = valid_label(batchId="batch-50", pairId="p1", annotator="reviewer-b")
    write_labels(path, [label, label])
    with pytest.raises(StoryAnnotationError) as error:
        validate_submission(manifest, path, "reviewer-b")
    assert error.value.code == "DUPLICATE_PAIR_LABEL"


def test_validate_submission_rejects_wrong_reviewer_and_unknown_pair(tmp_path):
    manifest = make_manifest(tmp_path)
    wrong = tmp_path / "wrong.jsonl"
    write_labels(wrong, [valid_label(batchId="batch-50", pairId="p1", annotator="reviewer-a")])
    with pytest.raises(StoryAnnotationError) as error:
        validate_submission(manifest, wrong, "reviewer-b")
    assert error.value.code == "WRONG_REVIEWER"
    unknown = tmp_path / "unknown.jsonl"
    write_labels(unknown, [valid_label(batchId="batch-50", pairId="p9", annotator="reviewer-b")])
    with pytest.raises(StoryAnnotationError) as error:
        validate_submission(manifest, unknown, "reviewer-b")
    assert error.value.code == "UNKNOWN_PAIR_ID"


def test_validate_submission_rejects_primary_outside_pair(tmp_path):
    manifest = make_manifest(tmp_path)
    path = tmp_path / "reviewer-a.jsonl"
    write_labels(path, [valid_label(batchId="batch-50", pairId="p1", primarySourceArticleId="outside")])
    with pytest.raises(StoryAnnotationError) as error:
        validate_submission(manifest, path, "reviewer-a")
    assert error.value.code == "PRIMARY_SOURCE_OUTSIDE_PAIR"


def test_validate_reviewer_b_reads_only_supplied_file(tmp_path, monkeypatch):
    manifest = make_manifest(tmp_path)
    b_path = tmp_path / "reviewer-b.jsonl"
    write_labels(
        b_path,
        [
            valid_label(batchId="batch-50", pairId="p1", annotator="reviewer-b"),
            valid_label(batchId="batch-50", pairId="p2", annotator="reviewer-b", primarySourceArticleId="c"),
        ],
    )
    original = type(b_path).read_bytes

    def guarded(path):
        assert "reviewer-a" not in str(path)
        return original(path)

    monkeypatch.setattr(type(b_path), "read_bytes", guarded)
    assert validate_submission(manifest, b_path, "reviewer-b").complete is True
