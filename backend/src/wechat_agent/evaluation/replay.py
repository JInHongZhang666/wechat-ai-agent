"""Deterministic distribution replay against fully human-labelled samples."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from wechat_agent.domain.models import PUBLIC_TOPIC_CATEGORIES, ArticleRecord
from wechat_agent.domain.relevance import is_distribution_eligible
from wechat_agent.evaluation.models import GoldSample
from wechat_agent.scoring.curation import select_curated
from wechat_agent.scoring.heat import score_articles
from wechat_agent.services.pipeline import DISTRIBUTION_STRATEGY_VERSION


def replay_samples(samples: list[GoldSample]) -> dict[str, Any]:
    """Replay deterministic scoring per collection group and return metrics."""
    article_ids = [sample.article.id for sample in samples]
    if len(article_ids) != len(set(article_ids)):
        raise ValueError("gold samples must use unique article ids")
    predictions: dict[str, dict[str, object]] = {}
    groups: dict[str, list[GoldSample]] = defaultdict(list)
    for sample in samples:
        groups[sample.evaluation_group].append(sample)

    for group_samples in groups.values():
        articles = [sample.article for sample in group_samples]
        evaluation_at = max(article.collected_at for article in articles)
        scores = score_articles(articles, evaluation_at)
        eligible = [article for article in articles if is_distribution_eligible(article)]
        selected_ids = {
            article.id
            for article in select_curated(
                eligible,
                scores,
            )
        }
        for article in articles:
            predictions[article.id] = {
                "relevant": is_distribution_eligible(article),
                "category": article.category.value,
                "selected": article.id in selected_ids,
                "score": scores[article.id].value,
            }

    relevance_truth = [sample.label.relevant for sample in samples]
    relevance_pred = [bool(predictions[sample.article.id]["relevant"]) for sample in samples]
    selected_truth = [sample.label.selected for sample in samples]
    selected_pred = [bool(predictions[sample.article.id]["selected"]) for sample in samples]
    category_pairs = [
        (sample.label.category.value, str(predictions[sample.article.id]["category"]))
        for sample in samples
        if sample.label.relevant
    ]

    mismatches = []
    for sample in samples:
        prediction = predictions[sample.article.id]
        changed = []
        if prediction["relevant"] != sample.label.relevant:
            changed.append("relevant")
        if prediction["selected"] != sample.label.selected:
            changed.append("selected")
        if sample.label.relevant and prediction["category"] != sample.label.category.value:
            changed.append("category")
        if changed:
            mismatches.append({
                "articleId": sample.article.id,
                "title": sample.article.title,
                "fields": changed,
                "prediction": prediction,
                "label": {
                    "relevant": sample.label.relevant,
                    "category": sample.label.category.value,
                    "selected": sample.label.selected,
                },
            })

    return {
        "schemaVersion": 1,
        "generatedAt": datetime.now(UTC).isoformat(),
        "mode": "deterministic",
        "runtime": {
            "model": None,
            "promptVersion": None,
            "tokenCount": 0,
            "estimatedCost": 0,
        },
        "strategyVersion": DISTRIBUTION_STRATEGY_VERSION,
        "sampleCount": len(samples),
        "groupCount": len(groups),
        "metrics": {
            "relevance": _binary_metrics(relevance_truth, relevance_pred),
            "selection": _binary_metrics(selected_truth, selected_pred),
            "category": _category_metrics(category_pairs),
        },
        "mismatchCount": len(mismatches),
        "mismatches": mismatches,
    }


def _binary_metrics(truth: list[bool], predicted: list[bool]) -> dict[str, float | int]:
    tp = sum(expected and actual for expected, actual in zip(truth, predicted, strict=True))
    fp = sum(not expected and actual for expected, actual in zip(truth, predicted, strict=True))
    fn = sum(expected and not actual for expected, actual in zip(truth, predicted, strict=True))
    tn = sum(not expected and not actual for expected, actual in zip(truth, predicted, strict=True))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        "truePositive": tp,
        "falsePositive": fp,
        "falseNegative": fn,
        "trueNegative": tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(
            2 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0,
            4,
        ),
        "accuracy": round((tp + tn) / len(truth) if truth else 0.0, 4),
    }


def _category_metrics(pairs: list[tuple[str, str]]) -> dict[str, object]:
    per_category: dict[str, dict[str, float | int]] = {}
    f1_values: list[float] = []
    for category in (value.value for value in PUBLIC_TOPIC_CATEGORIES):
        truth = [expected == category for expected, _ in pairs]
        predicted = [actual == category for _, actual in pairs]
        metrics = _binary_metrics(truth, predicted)
        per_category[category] = metrics
        f1_values.append(float(metrics["f1"]))
    accuracy = (
        sum(expected == actual for expected, actual in pairs) / len(pairs)
        if pairs
        else 0.0
    )
    return {
        "accuracy": round(accuracy, 4),
        "macroF1": round(sum(f1_values) / len(f1_values), 4),
        "perCategory": per_category,
    }
