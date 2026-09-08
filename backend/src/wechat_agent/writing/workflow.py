"""Deterministic creation workflow policies shared by API and persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from uuid import uuid4

from wechat_agent.domain.models import (
    AccountProfile,
    ContentOpportunity,
    DraftDocument,
    DraftVersion,
    EvidencePack,
    OpportunityScores,
    QualityIssue,
    QualityReportRecord,
    TopicCandidate,
)


def build_opportunity(
    topic: TopicCandidate,
    pack: EvidencePack,
    profile: AccountProfile,
) -> ContentOpportunity:
    unverified = {claim.strip().casefold() for claim in pack.unverified_claims}
    conflicting = {
        claim.strip().casefold()
        for conflict in pack.conflicts
        for claim in conflict.claims
    }
    verified = [
        fact
        for fact in pack.facts
        if fact.claim.strip().casefold() not in unverified | conflicting
    ]
    official_edges = {
        url
        for fact in verified
        for url, source in fact.sources.items()
        if source.source_type.value in {"official", "primary"}
        and source.source_type_verified
    }
    evidence_ratio = len(verified) / max(len(pack.facts), 1)
    evidence_score = min(100.0, evidence_ratio * 70 + min(len(official_edges), 2) * 15)
    category_text = topic.category.value.replace("-", " ").casefold()
    pillar_match = any(
        category_text in pillar.casefold() or pillar.casefold() in topic.title.casefold()
        for pillar in profile.content_pillars
    )
    heat_value = max(0.0, min(float(topic.heat_score.value), 100.0))
    scores = OpportunityScores(
        account_fit=92 if pillar_match else 78,
        audience_value=min(95, 70 + heat_value * 0.25),
        differentiation=85 if pack.suggested_angle else 65,
        event_impact=min(95, 65 + heat_value * 0.30),
        evidence_completeness=evidence_score,
        timeliness=85,
        production_efficiency=90 if pack.ready_for_draft else 55,
    )
    blockers: list[str] = []
    if not official_edges:
        blockers.append("缺少已验证的一手或官方来源")
    if not pack.ready_for_draft:
        blockers.append("证据包尚未通过正式写作门")
    if pack.conflicts:
        blockers.append("存在未解决的事实冲突")
    risks = [
        *[f"冲突：{conflict.subject}" for conflict in pack.conflicts],
        *[f"待核实：{claim}" for claim in pack.unverified_claims],
    ]
    return ContentOpportunity(
        id=f"opportunity-{sha256(topic.id.encode('utf-8')).hexdigest()[:16]}",
        source_id=topic.id,
        title=topic.title,
        category=topic.category.value,
        scores=scores,
        creation_score=scores.weighted_total,
        recommended_angle=pack.suggested_angle or topic.recommendation_reason,
        reader_benefit=topic.summary or "帮助读者快速判断这条信息是否值得关注和采用。",
        risks=risks,
        blockers=blockers,
        status="blocked" if blockers else "new",
    )


def prompt_hash(*parts: object) -> str:
    raw = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return sha256(raw.encode("utf-8")).hexdigest()


def build_draft_version(
    *,
    draft_id: str,
    version: int,
    parent_version: int | None,
    generation_method: str,
    document: DraftDocument,
    selected_title: str,
    profile_version: int,
    evidence: EvidencePack,
    author_input_ids: list[str] | None = None,
    model: str | None = None,
    change_summary: str = "",
    prompt_version_id: str | None = None,
    llm_call_ids: list[str] | None = None,
    generation_prompt_hash: str | None = None,
) -> DraftVersion:
    return DraftVersion(
        id=str(uuid4()),
        draft_id=draft_id,
        version=version,
        parent_version=parent_version,
        generation_method=generation_method,
        model=model,
        prompt_hash=generation_prompt_hash or prompt_hash(
            evidence.topic_id,
            [fact.claim_id for fact in evidence.facts],
            profile_version,
            generation_method,
        ),
        prompt_version_id=prompt_version_id,
        llm_call_ids=llm_call_ids or [],
        profile_version=profile_version,
        selected_title=selected_title,
        document=document.model_copy(deep=True),
        cited_claim_ids=[
            fact.claim_id
            for fact in evidence.facts
            if set(fact.source_urls) & set(document.cited_source_urls)
        ],
        author_input_ids=author_input_ids or [],
        change_summary=change_summary,
    )


def build_quality_report(
    *,
    draft_id: str,
    draft_version: int,
    profile_version: int,
    issues: list[QualityIssue],
    human_confirmed: bool = False,
) -> QualityReportRecord:
    return QualityReportRecord(
        id=str(uuid4()),
        draft_id=draft_id,
        draft_version=draft_version,
        profile_version=profile_version,
        issues=issues,
        passed=not any(issue.blocking for issue in issues),
        human_confirmed=human_confirmed,
        created_at=datetime.now(UTC),
    )


def confirmed_author_section(author_inputs) -> tuple[str, list[str]]:
    confirmed = [item for item in author_inputs if item.confirmed]
    if not confirmed:
        return "", []
    lines = ["## 作者补充"]
    labels = {
        "experience": "已确认经验",
        "opinion": "作者观点",
        "example": "作者案例",
        "data": "作者数据",
    }
    for item in confirmed:
        lines.append(f"**{labels[item.kind]}**：{item.content}")
    return "\n\n".join(lines), [item.id for item in confirmed]
