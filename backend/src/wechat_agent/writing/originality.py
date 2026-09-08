"""Deterministic, auditable overlap checks for inspiration-led drafts."""

from __future__ import annotations

import re
import unicodedata
from uuid import uuid4

from wechat_agent.domain.models import DraftDocument, InspirationSource, QualityIssue, SimilarityReport


SIMILARITY_THRESHOLD = 0.10
NGRAM_SIZE = 12
_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_URL = re.compile(r"https?://\S+")
_REFERENCE_SECTION = re.compile(r"(?:^|\n)#{1,3}\s*参考来源[\s\S]*$", re.IGNORECASE)


def evaluate_originality(
    document: DraftDocument,
    sources: list[InspirationSource],
    *,
    draft_id: str,
    draft_version: int,
    threshold: float = SIMILARITY_THRESHOLD,
) -> tuple[list[SimilarityReport], list[QualityIssue]]:
    """Compare draft-to-source containment; the primary source is a hard gate."""
    reports = [
        compare_with_source(
            document, source, draft_id=draft_id,
            draft_version=draft_version, threshold=threshold,
        )
        for source in sources
    ]
    primary_reports = [
        report for report, source in zip(reports, sources, strict=True)
        if source.role == "primary"
    ]
    issues: list[QualityIssue] = []
    if not primary_reports:
        issues.append(QualityIssue(
            code="originality_check_missing",
            message="当前创作项目缺少可复核的主灵感原文，不能证明原创度。",
            location="originality",
            fix_suggestion="重新导入可解析的热门文章或用户文章链接。",
        ))
    for report in primary_reports:
        if not report.passed:
            fragments = "；".join(report.matched_fragments[:2]) or "存在连续重复表达"
            issues.append(QualityIssue(
                code="inspiration_similarity_exceeded",
                message=f"草稿与主灵感文章的可解释重复率为 {report.score:.1%}，超过 10% 上限。重复片段：{fragments}",
                location="originality",
                fix_suggestion="保留事实引用，重新组织论证顺序、句式和表达；不要只做同义词替换。",
            ))
    return reports, issues


def compare_with_source(
    document: DraftDocument,
    source: InspirationSource,
    *,
    draft_id: str,
    draft_version: int,
    threshold: float = SIMILARITY_THRESHOLD,
) -> SimilarityReport:
    draft_text = _normalize(f"{document.title}\n{document.body}")[:15_000]
    source_text = _normalize(source.content_text)[:50_000]
    draft_ngrams = _ngrams(draft_text)
    source_ngrams = _ngrams(source_text)
    ngram_overlap = len(draft_ngrams & source_ngrams) / len(draft_ngrams) if draft_ngrams else 0.0
    longest_characters, fragments = _exact_matching_fragments(draft_text, source_text)
    longest_ratio = longest_characters / len(draft_text) if draft_text else 0.0
    score = min(1.0, max(ngram_overlap, longest_ratio))
    return SimilarityReport(
        id=str(uuid4()), draft_id=draft_id, draft_version=draft_version,
        source_id=source.id, source_url=source.url, threshold=threshold,
        score=round(score, 6), ngram_overlap=round(ngram_overlap, 6),
        longest_match_ratio=round(longest_ratio, 6), matched_fragments=fragments,
        passed=score <= threshold,
    )


def _normalize(value: str) -> str:
    value = _REFERENCE_SECTION.sub("", value)
    value = _LINK.sub(r"\1", value)
    value = _URL.sub("", value)
    value = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in value if character.isalnum())


def _ngrams(value: str) -> set[str]:
    if len(value) < NGRAM_SIZE:
        return {value} if value else set()
    return {value[index:index + NGRAM_SIZE] for index in range(len(value) - NGRAM_SIZE + 1)}


def _exact_matching_fragments(draft: str, source: str) -> tuple[int, list[str]]:
    """Find exact runs in bounded linear-ish time using shared n-gram diagonals."""
    if len(draft) < NGRAM_SIZE or len(source) < NGRAM_SIZE:
        return (len(draft) if draft and draft in source else 0, [draft] if draft and draft in source else [])
    source_positions: dict[str, list[int]] = {}
    for index in range(len(source) - NGRAM_SIZE + 1):
        positions = source_positions.setdefault(source[index:index + NGRAM_SIZE], [])
        if len(positions) < 20:
            positions.append(index)
    active_runs: dict[int, tuple[int, int]] = {}
    completed_runs: list[tuple[int, int]] = []
    for draft_index in range(len(draft) - NGRAM_SIZE + 1):
        for source_index in source_positions.get(draft[draft_index:draft_index + NGRAM_SIZE], []):
            diagonal = source_index - draft_index
            start, last = active_runs.get(diagonal, (draft_index, draft_index - 2))
            if draft_index > last + 1:
                if last >= start:
                    completed_runs.append((start, last))
                start = draft_index
            active_runs[diagonal] = (start, draft_index)
    completed_runs.extend(active_runs.values())
    candidates = sorted(
        ((last - start + NGRAM_SIZE, start) for start, last in completed_runs),
        reverse=True,
    )
    fragments: list[str] = []
    for length, start in candidates:
        fragment = draft[start:start + min(length, 80)]
        if length >= NGRAM_SIZE and fragment not in fragments:
            fragments.append(fragment)
        if len(fragments) == 5:
            break
    return (candidates[0][0] if candidates else 0, fragments)
