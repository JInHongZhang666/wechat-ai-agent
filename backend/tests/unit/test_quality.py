from wechat_agent.domain.models import (
    DraftDocument,
    EvidenceFact,
    EvidencePack,
    EvidenceSource,
    SourceType,
    TopicCategory,
)
from wechat_agent.writing.quality import check_quality


OFFICIAL_URL = "https://acme.example/announcement"
REPORT_URL = "https://news.example/acme"


def _pack(category: TopicCategory = TopicCategory.TOOL) -> EvidencePack:
    sources = {
        OFFICIAL_URL: EvidenceSource(
            source_name="Acme",
            source_identity="acme",
            source_type=SourceType.OFFICIAL,
            source_type_verified=True,
            provenance="verified domain ownership",
        ),
        REPORT_URL: EvidenceSource(
            source_name="News Example",
            source_identity="news.example",
        ),
    }
    return EvidencePack(
        topic_id="topic-1",
        topic_title="Acme API 更新",
        category=category,
        facts=[
            EvidenceFact(
                claim="Model 3 supports 10 languages.",
                source_urls=list(sources),
                sources=sources,
            )
        ],
        source_urls=list(sources),
        ready_for_draft=True,
    )


def _draft(
    *,
    body: str = "## 核心信息\n\n**可靠信息**：Model 3 supports 10 languages.",
    titles: list[str] | None = None,
    cited_source_urls: list[str] | None = None,
) -> DraftDocument:
    return DraftDocument(
        titles=titles
        or [
            "Acme API 更新：真正值得关注的变化",
            "别只看热度，读懂 Acme API 更新",
            "Acme API 更新之后，谁更适合使用",
            "一次说清 Acme API 更新的价值与边界",
            "Acme API 更新来了，先看这几个判断",
        ],
        body=body,
        cited_source_urls=(
            [OFFICIAL_URL, REPORT_URL]
            if cited_source_urls is None
            else cited_source_urls
        ),
        stage="research",
    )


def test_editable_unsourced_draft_is_valid_but_blocked_from_review_and_export():
    draft = DraftDocument(
        titles=["标题"],
        body="字" * 81,
        cited_source_urls=[],
        stage="research",
    )

    issues = check_quality(draft, _pack())
    by_code = {issue.code: issue for issue in issues}

    assert {"paragraph_too_long", "missing_source", "weak_title", "insufficient_sources"} <= set(
        by_code
    )
    assert all(by_code[code].blocking for code in ("missing_source", "insufficient_sources"))
    assert draft.cited_source_urls == []


def test_quality_flags_unverified_personal_test_for_tool():
    issues = check_quality(_draft(body="这是我亲测后的结论。"), _pack())

    assert any(
        issue.code == "unverified_personal_test" and issue.blocking
        for issue in issues
    )


def test_quality_flags_natural_personal_experience_phrasing_for_tool():
    experience_bodies = (
        "我体验了一周后，认为它足以替代原有方案。",
        "上手两天的感受是，流程比预期顺畅。",
    )

    for body in experience_bodies:
        issues = check_quality(_draft(body=body), _pack())

        assert "unverified_personal_test" in {issue.code for issue in issues}


def test_quality_allows_non_personal_usability_language_for_tool():
    issues = check_quality(
        _draft(body="该功能可以降低上手门槛，并改善用户体验。"),
        _pack(),
    )

    assert "unverified_personal_test" not in {issue.code for issue in issues}


def test_quality_flags_only_numbers_not_supported_by_evidence_facts():
    supported = check_quality(
        _draft(body="Model 3 supports 10 languages."), _pack()
    )
    unsupported = check_quality(
        _draft(body="Model 3 supports 12 languages."), _pack()
    )

    assert "unsupported_number" not in {issue.code for issue in supported}
    assert "unsupported_number" in {issue.code for issue in unsupported}


def test_quality_does_not_treat_ordered_list_marker_as_factual_number():
    issues = check_quality(
        _draft(body="1. 先确认需求\n\nModel 3 supports 10 languages."),
        _pack(),
    )

    assert "unsupported_number" not in {issue.code for issue in issues}


def test_quality_does_not_treat_markdown_link_target_number_as_a_fact():
    issues = check_quality(
        _draft(body="详见[官方公告](https://acme.example/models/gpt-5)。"),
        _pack(),
    )

    assert "unsupported_number" not in {issue.code for issue in issues}


def test_quality_rejects_citation_outside_fact_support_edges():
    issues = check_quality(
        _draft(cited_source_urls=[OFFICIAL_URL, "https://unrelated.example/item"]),
        _pack(),
    )

    assert any(issue.code == "missing_source" and issue.blocking for issue in issues)


def test_quality_reports_insufficient_independent_publishers():
    one_source = _pack()
    one_source.facts = [
        EvidenceFact(
            claim="Model 3 supports 10 languages.",
            source_urls=[OFFICIAL_URL],
            sources={
                OFFICIAL_URL: EvidenceSource(
                    source_name="Acme",
                    source_identity="acme",
                    source_type=SourceType.OFFICIAL,
                    source_type_verified=True,
                    provenance="verified domain ownership",
                )
            },
        )
    ]
    one_source.source_urls = [OFFICIAL_URL]
    one_source.ready_for_draft = False

    issues = check_quality(
        _draft(cited_source_urls=[OFFICIAL_URL]),
        one_source,
    )

    assert any(issue.code == "insufficient_sources" and issue.blocking for issue in issues)


def test_clean_research_draft_has_no_blocking_issues():
    issues = check_quality(_draft(), _pack())

    assert [issue for issue in issues if issue.blocking] == []


def test_quality_blocks_body_that_differs_from_trusted_generated_body():
    issues = check_quality(
        _draft(body="该项目已经永久关闭所有服务，用户数据已全部泄露。"),
        _pack(),
        trusted_body="## 核心信息\n\n**可靠信息**：Model 3 supports 10 languages.",
    )

    assert any(
        issue.code == "edited_content_requires_verification"
        and issue.blocking
        for issue in issues
    )


def test_quality_allows_unchanged_or_restored_trusted_body():
    trusted_body = "## 核心信息\n\n**可靠信息**：Model 3 supports 10 languages."

    issues = check_quality(
        _draft(body=trusted_body),
        _pack(),
        trusted_body=trusted_body,
    )

    assert "edited_content_requires_verification" not in {
        issue.code for issue in issues
    }


def test_quality_blocks_selected_title_outside_trusted_generated_titles():
    trusted_titles = tuple(_draft().titles)
    issues = check_quality(
        _draft(
            titles=[
                "未经证据支持的新断言标题",
                *trusted_titles[:4],
            ]
        ),
        _pack(),
        trusted_titles=trusted_titles,
    )

    assert any(
        issue.code == "edited_title_requires_verification"
        and issue.blocking
        for issue in issues
    )


def test_quality_allows_selected_title_from_trusted_generated_titles():
    trusted_titles = tuple(_draft().titles)
    selected = trusted_titles[1]
    issues = check_quality(
        _draft(
            titles=[
                selected,
                *[title for title in trusted_titles if title != selected],
            ]
        ),
        _pack(),
        trusted_titles=trusted_titles,
    )

    assert "edited_title_requires_verification" not in {
        issue.code for issue in issues
    }


def test_human_reviewed_edits_remain_visible_as_non_blocking_audit_items():
    trusted = _draft()
    issues = check_quality(
        _draft(
            titles=["Acme API 更新后的使用边界", *trusted.titles[1:]],
            body="## 核心信息\n\n**可靠信息**：Model 3 supports 10 languages.\n\n这段由作者重新组织。",
        ),
        _pack(),
        trusted_body=trusted.body,
        trusted_titles=tuple(trusted.titles),
        allow_human_reviewed_edits=True,
    )

    audit_items = {
        issue.code: issue
        for issue in issues
        if issue.code in {"manual_title_review_required", "manual_content_review_required"}
    }
    assert set(audit_items) == {
        "manual_title_review_required",
        "manual_content_review_required",
    }
    assert all(not issue.blocking for issue in audit_items.values())
