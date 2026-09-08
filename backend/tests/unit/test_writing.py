import re
from unittest.mock import MagicMock, patch

from wechat_agent.domain.models import (
    DraftDocument,
    EvidenceFact,
    EvidencePack,
    EvidenceSource,
    SourceType,
    TopicCategory,
    WritingProfile,
)
from wechat_agent.writing.provider import (
    LlmDraftProvider,
    RuleBasedDraftProvider,
    _build_user_prompt,
    _parse_llm_response,
    classify_llm_error,
)
from wechat_agent.writing.quality import check_quality


OFFICIAL_URL = "https://acme.example/announcement"
REPORT_URL = "https://news.example/acme"


def _pack(category: TopicCategory) -> EvidencePack:
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
    fact = EvidenceFact(
        claim="Acme API supports 10 languages.",
        source_urls=list(sources),
        sources=sources,
    )
    return EvidencePack(
        topic_id="topic-1",
        topic_title="Acme API 更新",
        category=category,
        facts=[fact],
        source_urls=list(sources),
        suggested_angle="从真实能力、适用场景和采用边界解释这次更新。",
        ready_for_draft=True,
    )


def _visible_length(paragraph: str) -> int:
    return len(re.sub(r"[*#`\s]", "", paragraph))


def test_rule_provider_is_deterministic_and_emits_five_tool_titles():
    pack = _pack(TopicCategory.TOOL)
    provider = RuleBasedDraftProvider()
    profile = WritingProfile(account_name="AI信息小屋")

    first = provider.generate(pack, profile)
    second = provider.generate(pack, profile)

    assert first == second
    assert len(first.titles) == 5
    assert len(set(first.titles)) == 5
    assert "**" in first.body
    assert "## 工具是什么" in first.body
    assert first.stage == "research"


def test_unverified_tool_draft_never_claims_personal_testing():
    draft = RuleBasedDraftProvider().generate(
        _pack(TopicCategory.TOOL), WritingProfile()
    )

    assert draft.stage == "research"
    assert not any(word in draft.body for word in ("亲测", "实测", "我试用", "我用了"))


def test_generated_draft_uses_only_fact_support_edges_and_mobile_paragraphs():
    draft = RuleBasedDraftProvider().generate(
        _pack(TopicCategory.TOOL), WritingProfile()
    )
    paragraphs = [
        paragraph
        for paragraph in draft.body.split("\n\n")
        if paragraph and not paragraph.startswith("## ")
    ]
    chinese_characters = re.findall(r"[\u4e00-\u9fff]", draft.body)

    assert set(draft.cited_source_urls) == {OFFICIAL_URL, REPORT_URL}
    assert all(_visible_length(paragraph) <= 80 for paragraph in paragraphs)
    assert 650 <= len(chinese_characters) <= 950


def test_version_in_provider_topic_is_not_an_unsupported_number():
    pack = _pack(TopicCategory.TOOL)
    pack.topic_title = "GPT-5 release"
    draft = RuleBasedDraftProvider().generate(pack, WritingProfile())

    assert "unsupported_number" not in {
        issue.code for issue in check_quality(draft, pack)
    }

    draft.body += "\n\n成本5元。"

    assert "unsupported_number" in {
        issue.code for issue in check_quality(draft, pack)
    }


def test_news_draft_uses_news_specific_structure():
    draft = RuleBasedDraftProvider().generate(
        _pack(TopicCategory.NEWS), WritingProfile()
    )

    assert "## 发生了什么" in draft.body
    assert "## 为什么值得关注" in draft.body
    assert "## 工具是什么" not in draft.body


def test_draft_document_accepts_legacy_fields_without_rejecting_empty_sources():
    draft = DraftDocument(
        title="旧标题",
        content="仍可编辑的正文",
        source_urls=[],
    )

    assert draft.titles == ["旧标题"]
    assert draft.body == "仍可编辑的正文"
    assert draft.cited_source_urls == []
    assert draft.title == "旧标题"
    assert draft.content == "仍可编辑的正文"
    assert draft.source_urls == []


# ---------------------------------------------------------------------------
# LLM Draft Provider tests
# ---------------------------------------------------------------------------


def test_llm_provider_falls_back_on_api_error():
    """When the LLM API fails, the provider should fall back to the rule engine."""
    config = MagicMock()
    config.api_key = "test-key"
    config.base_url = "https://api.example.com/v1"
    config.model = "test-model"
    config.temperature = 0.7
    config.max_tokens = 4096
    config.request_timeout = 30.0

    provider = LlmDraftProvider(config)
    # Mock the OpenAI client's chat.completions.create to raise an exception
    provider._client.chat.completions.create = MagicMock(
        side_effect=Exception("API error")
    )

    pack = _pack(TopicCategory.TOOL)
    draft = provider.generate(pack, WritingProfile(account_name="AI信息小屋"))

    # Fallback should still produce valid output
    assert len(draft.titles) == 5
    assert "**" in draft.body
    assert draft.stage == "research"


def test_llm_provider_exposes_fallback_provenance_on_api_error():
    config = MagicMock(api_key="test-key", base_url="https://api.example.com/v1")
    config.model = "test-model"
    config.temperature = 0.7
    config.max_tokens = 4096
    config.request_timeout = 30.0
    provider = LlmDraftProvider(config)
    provider._client.chat.completions.create = MagicMock(side_effect=TimeoutError("timeout"))

    outcome = provider.generate_with_audit(_pack(TopicCategory.TOOL), WritingProfile())

    assert outcome.method == "rule"
    assert outcome.status == "fallback"
    assert outcome.prompt_version_id == "creation-fallback-v2"
    assert outcome.call_ids


def test_llm_runtime_probe_records_success_without_creating_a_draft():
    config = MagicMock(api_key="test-key", base_url="https://api.example.com/v1")
    config.model = "test-model"
    config.max_tokens = 4096
    config.request_timeout = 30.0
    audit = MagicMock()
    provider = LlmDraftProvider(config, audit)
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content='{"ok":true}'))]
    response.usage = MagicMock(prompt_tokens=12, completion_tokens=5)
    provider._client.chat.completions.create = MagicMock(return_value=response)

    record = provider.probe()

    assert record.stage == "probe"
    assert record.status == "success"
    assert record.model == "test-model"
    assert record.input_tokens == 12
    assert record.output_tokens == 5
    audit.save_call.assert_called_once_with(record)


def test_llm_runtime_probe_classifies_timeout_for_operator_diagnostics():
    config = MagicMock(api_key="test-key", base_url="https://api.example.com/v1")
    config.model = "test-model"
    config.max_tokens = 4096
    config.request_timeout = 30.0
    audit = MagicMock()
    provider = LlmDraftProvider(config, audit)
    provider._client.chat.completions.create = MagicMock(side_effect=TimeoutError("slow"))

    record = provider.probe()

    assert record.stage == "probe"
    assert record.status == "error"
    assert record.error_code == "LLM_TIMEOUT"
    assert record.fallback_reason == "slow"
    audit.save_call.assert_called_once_with(record)


def test_llm_error_classifier_reports_insufficient_balance():
    error = Exception("Insufficient Balance")
    error.status_code = 402

    assert classify_llm_error(error) == "LLM_INSUFFICIENT_BALANCE"


def test_llm_provider_parses_valid_json_response():
    """When the LLM returns valid JSON, parse it correctly."""
    config = MagicMock()
    config.api_key = "test-key"
    config.base_url = "https://api.example.com/v1"
    config.model = "test-model"
    config.temperature = 0.7
    config.max_tokens = 4096
    config.request_timeout = 30.0

    provider = LlmDraftProvider(config)
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(
            message=MagicMock(
                content='{"titles":["测试标题1","测试标题2","测试标题3","测试标题4","测试标题5"],"body":"## 发生了什么\\n\\n正文内容。","cited_source_urls":["https://acme.example/announcement"]}'
            )
        )
    ]
    provider._client.chat.completions.create = MagicMock(
        return_value=mock_response
    )

    pack = _pack(TopicCategory.TOOL)
    draft = provider.generate(pack, WritingProfile(account_name="AI信息小屋"))

    assert len(draft.titles) == 5
    assert "测试标题1" in draft.titles
    assert "正文内容" in draft.body
    assert "https://acme.example/announcement" in draft.cited_source_urls
    assert draft.stage == "research"


def test_generation_prompt_includes_user_selected_writing_controls():
    profile = WritingProfile(
        writing_style="轻松口语",
        creation_mode="rewrite",
        rewrite_goal="more_actionable",
        prompt_variant="classic",
        reference_focus="opening",
        reference_level="low",
        custom_instruction="先给普通读者可执行结论。",
    )

    prompt = _build_user_prompt(_pack(TopicCategory.TOOL), profile)

    assert "表达风格：轻松口语" in prompt
    assert "创作模式：参考重写" in prompt
    assert "改写目标：增强可执行性" in prompt
    assert "提示词策略：经典稳重" in prompt
    assert "参考重点：opening（强度：low）" in prompt
    assert "先给普通读者可执行结论" in prompt
    assert "不得覆盖事实边界、来源要求和原创硬门" in prompt


def test_polish_uses_current_draft_and_keeps_evidence_sources():
    config = MagicMock(api_key="test-key", base_url="https://api.example.com/v1")
    config.model = "test-model"
    config.temperature = 0.2
    config.max_tokens = 4096
    config.request_timeout = 30.0
    provider = LlmDraftProvider(config)
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=(
        '{"titles":["润色标题一","润色标题二","润色标题三","润色标题四","润色标题五"],'
        '"body":"## 新开头\\n\\n更自然的正文。",'
        '"cited_source_urls":["https://acme.example/announcement"]}'
    )))]
    provider._client.chat.completions.create = MagicMock(return_value=response)
    original = DraftDocument(
        titles=["原始标题"],
        body="## 原始开头\n\n原始正文。",
        cited_source_urls=[OFFICIAL_URL],
    )

    result = provider.polish_with_audit(
        _pack(TopicCategory.TOOL),
        WritingProfile(writing_style="专业理性"),
        original,
        project_id="project-1",
        draft_id="draft-1",
    )

    sent_prompt = provider._client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "原始正文" in sent_prompt
    assert "删除模板化开场" in sent_prompt
    assert result.method == "ai"
    assert result.document.body.startswith("## 新开头")
    assert result.document.cited_source_urls == [OFFICIAL_URL]


def test_parse_llm_response_pads_titles_when_under_five():
    pack = _pack(TopicCategory.TOOL)
    data = {"titles": ["只有一个"], "body": "正文", "cited_source_urls": []}
    draft = _parse_llm_response(data, pack)
    assert len(draft.titles) == 5
    assert draft.titles[0] == "只有一个"


def test_parse_llm_response_filters_invalid_cited_urls():
    pack = _pack(TopicCategory.TOOL)
    data = {
        "titles": ["A", "B", "C", "D", "E"],
        "body": "正文",
        "cited_source_urls": [
            "https://acme.example/announcement",
            "https://fake.example/not-in-pack",
        ],
    }
    draft = _parse_llm_response(data, pack)
    assert "https://acme.example/announcement" in draft.cited_source_urls
    assert "https://fake.example/not-in-pack" not in draft.cited_source_urls


def test_parse_llm_response_provides_fallback_body_when_empty():
    pack = _pack(TopicCategory.TOOL)
    data = {"titles": ["A", "B", "C", "D", "E"], "body": "", "cited_source_urls": []}
    draft = _parse_llm_response(data, pack)
    assert "研究初稿" in draft.body
    assert draft.stage == "research"
