from datetime import UTC, datetime
import socket

import pytest

from wechat_agent.domain.models import (
    DraftDocument,
    InspirationSource,
    InspirationStructure,
    SourceType,
    TopicCategory,
)
from wechat_agent.writing.inspiration import (
    InspirationImportError,
    analyse_structure,
    extract_article,
    source_from_manual_text,
    validate_external_url,
)
from wechat_agent.writing.originality import compare_with_source, evaluate_originality


def _resolver_for(address: str):
    def resolve(host, port, *, type):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]
    return resolve


def _source(content: str) -> InspirationSource:
    return InspirationSource(
        id="primary", role="primary", source_kind="user_url",
        url="https://public.example/article", title="参考文章",
        publisher="公开来源", publisher_identity="publisher:public",
        content_text=content, content_sha256="a" * 64,
        source_type=SourceType.SECONDARY, category=TopicCategory.INDUSTRY,
        imported_at=datetime.now(UTC), structure=InspirationStructure(),
    )


def test_url_validation_blocks_private_addresses_and_credentials():
    with pytest.raises(InspirationImportError) as private:
        validate_external_url("http://internal.example/admin", resolver=_resolver_for("127.0.0.1"))
    assert private.value.code == "PRIVATE_URL_BLOCKED"
    with pytest.raises(InspirationImportError) as credentials:
        validate_external_url("https://user:secret@public.example/article", resolver=_resolver_for("8.8.8.8"))
    assert credentials.value.code == "INVALID_URL"


def test_url_validation_accepts_only_resolved_public_http_urls():
    value = validate_external_url(
        "HTTPS://Public.Example/article?x=1#fragment",
        resolver=_resolver_for("8.8.8.8"),
    )
    assert value == "https://public.example/article?x=1"


def test_article_extraction_returns_text_and_abstract_structure_only():
    title, content, structure, metadata = extract_article("""
    <html><head><title>测试文章</title><meta property="og:site_name" content="测试媒体"></head>
    <body><article><h1>测试文章</h1><p>这是第一段，直接说明文章发生了什么以及为什么值得关注。</p>
    <h2>核心信息</h2><p>这是第二段，补充足够长的公开资料内容，用来验证正文提取和结构分析逻辑。</p>
    <ul><li>第一项相关事实和说明。</li><li>第二项相关事实和说明。</li></ul>
    <p>最后给出边界、适用人群以及接下来值得继续观察的问题。</p></article></body></html>
    """)
    assert title == "测试文章"
    assert "这是第一段" in content
    assert metadata["publisher"] == "测试媒体"
    assert structure.heading_count == 2
    assert structure.list_item_count == 2
    assert structure.reusable_blueprint


def test_manual_text_fallback_keeps_structure_but_not_verified_evidence():
    source = source_from_manual_text(
        "HTTPS://Public.Example/article#section",
        """# 一个新的观察角度

这里是用户补充的参考正文。它只提供主题、结构与表达线索，不会自动成为事实证据。

## 文章如何展开

- 先说明公开背景以及普通读者为什么需要关注。
- 再拆解产品能力、适用条件、采用成本和潜在限制。

最后给出独立判断，并提醒所有关键事实仍需要由其他公开来源交叉核验。
为了满足有效正文长度，这里继续补充一段完整说明，避免只有标题或摘要时错误建立创作项目。""",
        title="用户补充的完整参考文章",
        resolver=_resolver_for("8.8.8.8"),
    )

    assert source.url == "https://public.example/article"
    assert source.title == "用户补充的完整参考文章"
    assert source.structure.heading_count == 2
    assert source.structure.list_item_count == 2
    assert source.structure.paragraph_count >= 5
    assert source.source_type_verified is False
    assert source.source_verification_basis is None
    assert len(source.content_sha256) == 64


def test_manual_text_fallback_rejects_too_little_content():
    with pytest.raises(InspirationImportError) as error:
        source_from_manual_text(
            "https://public.example/article",
            "只有一个很短的摘要。",
            resolver=_resolver_for("8.8.8.8"),
        )
    assert error.value.code == "ARTICLE_CONTENT_TOO_SHORT"


def test_originality_gate_blocks_copying_and_passes_independent_expression():
    source = _source(
        "这是一篇关于人工智能产品落地的参考文章。它逐项讨论团队成本、真实场景、"
        "使用边界和下一步行动，强调先验证事实再作判断。" * 20
    )
    copied = DraftDocument(
        titles=["参考文章"], body=source.content_text[:500], cited_source_urls=[]
    )
    reports, issues = evaluate_originality(copied, [source], draft_id="draft", draft_version=1)
    assert reports[0].score > 0.10
    assert any(issue.code == "inspiration_similarity_exceeded" for issue in issues)

    original = DraftDocument(
        titles=["落地之前先回答三个问题"],
        body=("## 判断起点\n\n供应团队已经公开了产品信息。本文结合独立资料，"
              "重新分析读者的采用条件与风险，并把尚未确认的部分留作后续观察。" * 12),
        cited_source_urls=[],
    )
    report = compare_with_source(original, source, draft_id="draft", draft_version=2)
    assert report.score <= 0.10
    assert report.passed is True
