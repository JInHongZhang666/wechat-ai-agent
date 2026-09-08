"""Build traceable evidence packs from collected article summaries."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
import re
from urllib.parse import urlsplit

from wechat_agent.domain.models import (
    ArticleRecord,
    EvidenceConflict,
    EvidenceFact,
    EvidencePack,
    EvidenceSource,
    SourceType,
    TopicCandidate,
)


_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?。！？])\s*")
_WHITESPACE = re.compile(r"\s+")
_TRAILING_PUNCTUATION = re.compile(r"[.!?。！？]+$")
_STRUCTURED_NUMERIC_CLAIM = re.compile(
    r"^(?P<subject>.+?)\s+"
    r"(?P<action>supports?|offers?|provides?|includes?|has|uses?|costs?|"
    r"requires?|reaches?|handles?)\s+"
    r"(?P<value>\d+(?:\.\d+)?)\s+"
    r"(?P<object>.+)$"
)
_NUMBER_VALUE = re.compile(r"\d+(?:[,.]\d+)*(?:[%％])?")
_DATE_VALUE = re.compile(r"(?:20\d{2}[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?|\d{1,2}月\d{1,2}日)")
_VERSION_VALUE = re.compile(r"\b(?:v|V)?\d+(?:\.\d+){1,3}(?:[-_][A-Za-z0-9.]+)?\b")


def build_evidence_pack(
    candidate: TopicCandidate, related: Sequence[ArticleRecord]
) -> EvidencePack:
    """Group exact normalized summary sentences without inferring new facts."""
    claims: dict[str, list[tuple[str, ArticleRecord]]] = defaultdict(list)

    for article in related:
        for sentence in _claim_sentences(article.summary):
            claims[_normalize_claim(sentence)].append((sentence, article))

    facts = [_fact_for_claim(entries) for _, entries in sorted(claims.items())]
    conflicts, conflicting_claims = _find_conflicts(claims)
    unverified_claims = [
        fact.claim
        for fact in facts
        if not _is_verified_fact(
            _normalize_claim(fact.claim),
            claims[_normalize_claim(fact.claim)],
            conflicting_claims,
        )
    ]
    unverified_normalized = {_normalize_claim(claim) for claim in unverified_claims}
    facts = [
        fact.model_copy(
            update={
                "status": (
                    "conflicting"
                    if _normalize_claim(fact.claim) in conflicting_claims
                    else "unverified"
                    if _normalize_claim(fact.claim) in unverified_normalized
                    else "verified"
                ),
            }
        )
        for fact in facts
    ]
    ordered_sources = sorted(
        {source_url for fact in facts for source_url in fact.source_urls}
    )

    return EvidencePack(
        topic_id=candidate.id,
        topic_title=candidate.title,
        category=candidate.category,
        facts=facts,
        source_urls=ordered_sources,
        conflicts=conflicts,
        unverified_claims=unverified_claims,
        suggested_angle=(
            f"基于多来源证据梳理「{candidate.title}」：先说明已获一手或官方来源"
            "支撑的事实，再标注待核实信息。"
        ),
        ready_for_draft=bool(facts) and not conflicts and not unverified_claims,
    )


def _claim_sentences(summary: str) -> list[str]:
    return [
        sentence.strip()
        for sentence in _SENTENCE_BOUNDARY.split(summary.strip())
        if sentence.strip()
    ]


def _normalize_claim(claim: str) -> str:
    return _TRAILING_PUNCTUATION.sub("", _WHITESPACE.sub(" ", claim).strip()).lower()


def _fact_for_claim(entries: list[tuple[str, ArticleRecord]]) -> EvidenceFact:
    claim, _ = entries[0]
    sources_by_url: dict[str, EvidenceSource] = {}
    for _, article in entries:
        sources_by_url.setdefault(
            article.source_url,
            EvidenceSource(
                source_name=article.source_name,
                source_identity=_publisher_identity(article),
                source_type=article.source_type,
                source_type_verified=article.source_type_verified,
                provenance=article.source_verification_basis,
            ),
        )

    source_urls = sorted(sources_by_url)
    sources = {source_url: sources_by_url[source_url] for source_url in source_urls}
    first_source = sources[source_urls[0]]
    return EvidenceFact(
        claim=claim,
        fact_type=_fact_type(claim),
        subject=(_parse_structured_claim(_normalize_claim(claim)) or (claim, "", "", ""))[0],
        numbers=_NUMBER_VALUE.findall(claim),
        dates=_DATE_VALUE.findall(claim),
        versions=_VERSION_VALUE.findall(claim),
        last_verified_at=max(article.collected_at for _, article in entries),
        source_name=first_source.source_name,
        source_url=source_urls[0],
        source_urls=source_urls,
        sources=sources,
    )


def _fact_type(claim: str) -> str:
    if _DATE_VALUE.search(claim):
        return "date"
    if _VERSION_VALUE.search(claim):
        return "version"
    if _NUMBER_VALUE.search(claim):
        return "number"
    return "fact"


def _find_conflicts(
    claims: dict[str, list[tuple[str, ArticleRecord]]],
) -> tuple[list[EvidenceConflict], set[str]]:
    by_subject: dict[
        tuple[str, str, str],
        dict[str, list[tuple[str, list[tuple[str, ArticleRecord]]]]],
    ] = defaultdict(lambda: defaultdict(list))
    for normalized_claim, entries in claims.items():
        parsed = _parse_structured_claim(normalized_claim)
        if parsed is None:
            continue
        subject, action, value, object_name = parsed
        by_subject[(subject, action, object_name)][value].append(
            (normalized_claim, entries)
        )

    conflicts: list[EvidenceConflict] = []
    conflicting_claims: set[str] = set()
    for (subject, action, object_name), variants_by_value in sorted(
        by_subject.items()
    ):
        if len(variants_by_value) < 2:
            continue
        variants = sorted(
            (
                variant
                for value_variants in variants_by_value.values()
                for variant in value_variants
            ),
            key=lambda variant: variant[0],
        )
        conflicting_claims.update(
            normalized_claim for normalized_claim, _ in variants
        )
        conflicts.append(
            EvidenceConflict(
                subject=f"{subject} {action} {{value}} {object_name}",
                claims=[entries[0][0] for _, entries in variants],
                source_urls=sorted(
                    {
                        article.source_url
                        for _, entries in variants
                        for _, article in entries
                    }
                ),
            )
        )
    return conflicts, conflicting_claims


def _parse_structured_claim(
    normalized_claim: str,
) -> tuple[str, str, str, str] | None:
    match = _STRUCTURED_NUMERIC_CLAIM.fullmatch(normalized_claim)
    if match is None:
        return None
    return (
        match.group("subject"),
        match.group("action"),
        match.group("value"),
        match.group("object"),
    )


def _publisher_identity(article: ArticleRecord) -> str:
    if article.source_identity and article.source_identity.strip():
        return _normalize_identity(article.source_identity)
    host = (urlsplit(article.source_url).hostname or article.source_url).casefold()
    return f"{_normalize_identity(article.source_name)}|{host}"


def _normalize_identity(value: str) -> str:
    return _WHITESPACE.sub(" ", value.strip()).casefold()


def _has_authoritative_source(entries: list[tuple[str, ArticleRecord]]) -> bool:
    return any(
        article.source_type in {SourceType.OFFICIAL, SourceType.PRIMARY}
        and article.source_type_verified
        and bool(
            article.source_verification_basis
            and article.source_verification_basis.strip()
        )
        for _, article in entries
    )


def _is_verified_fact(
    normalized_claim: str,
    entries: list[tuple[str, ArticleRecord]],
    conflicting_claims: set[str],
) -> bool:
    return (
        _parse_structured_claim(normalized_claim) is not None
        and normalized_claim not in conflicting_claims
        and len({_publisher_identity(article) for _, article in entries}) >= 2
        and _has_authoritative_source(entries)
    )
