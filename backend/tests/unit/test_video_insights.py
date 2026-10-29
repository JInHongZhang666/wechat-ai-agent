from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from wechat_agent.config import LlmConfig
from wechat_agent.video.insights import VideoInsightError, VideoInsightService
from wechat_agent.video.models import VideoQuestionTurn


def _client_with(content: str):
    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )
    return client


def _config() -> LlmConfig:
    return LlmConfig(api_key="test", model="deepseek-chat", max_tokens=4096)


def test_refine_keeps_source_isolated_and_returns_model_output():
    client = _client_with("真正有用的内容。\n另一个事实。")
    service = VideoInsightService(_config(), client=client)

    result = service.refine("口头禅。\n真正有用的内容。\n另一个事实。")

    assert result == "真正有用的内容。\n另一个事实。"
    request = client.chat.completions.create.call_args.kwargs
    assert request["model"] == "deepseek-chat"
    assert request["temperature"] == 0
    assert "不是总结、改写或润色" in request["messages"][0]["content"]
    assert "不得改动任何字词或标点" in request["messages"][0]["content"]
    assert "请求点赞、收藏、关注、转发" in request["messages"][0]["content"]
    assert "作者自我宣传" in request["messages"][0]["content"]
    assert "即使语句完整也不要保留" in request["messages"][0]["content"]
    assert "不能只因出现" in request["messages"][0]["content"]
    assert "介绍产品功能、开源属性、运营方法" in request["messages"][0]["content"]
    assert "作者抖音号、账号ID、用户ID" in request["messages"][0]["content"]
    assert "<原字稿>" in request["messages"][1]["content"]
    assert "真正有用的内容" in request["messages"][1]["content"]


def test_answer_is_grounded_in_refined_transcript_and_history():
    client = _client_with("稿件指出核心步骤有三项。")
    service = VideoInsightService(_config(), client=client)

    result = service.answer(
        "核心步骤有三项。",
        "有几项？",
        [VideoQuestionTurn(role="user", content="先总结一下")],
    )

    assert result == "稿件指出核心步骤有三项。"
    messages = client.chat.completions.create.call_args.kwargs["messages"]
    assert "只能依据提供的有效原稿回答" in messages[0]["content"]
    assert "核心步骤有三项" in messages[1]["content"]
    assert messages[-2] == {"role": "user", "content": "先总结一下"}
    assert messages[-1] == {"role": "user", "content": "有几项？"}


def test_empty_transcript_is_rejected_before_calling_model():
    client = _client_with("unused")
    service = VideoInsightService(_config(), client=client)

    with pytest.raises(VideoInsightError) as captured:
        service.refine("   ")

    assert captured.value.code == "VIDEO_TRANSCRIPT_EMPTY"
    client.chat.completions.create.assert_not_called()


def test_model_summary_is_rejected_when_it_is_not_a_verbatim_source_span():
    client = _client_with("这段内容主要讲了一个事实。")
    service = VideoInsightService(_config(), client=client)

    with pytest.raises(VideoInsightError) as captured:
        service.refine("原文中的一个事实。")

    assert captured.value.code == "VIDEO_SELECTION_INVALID"


def test_douyin_account_watermark_and_standalone_id_are_removed():
    source = "\n".join(
        [
            "抖音号：52789219181真正有用的内容。",
            "52789219181",
            "另一个事实。",
            "抖音",
            "来抖音发现更多创作者",
            "已经免费开源到抖音号：52789219181抖音",
        ]
    )
    client = _client_with("真正有用的内容。\n52789219181\n另一个事实。\n抖音\n来抖音发现更多创作者")
    service = VideoInsightService(_config(), client=client)

    result = service.refine(source)

    assert result == "真正有用的内容。\n另一个事实。"
    prompt_source = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "52789219181" not in prompt_source
    assert "来抖音发现更多创作者" not in prompt_source
    assert "已经免费开源到" not in prompt_source


def test_topic_discussion_of_douyin_account_without_identifier_is_preserved():
    source = "抖音号可以修改一次。"
    client = _client_with(source)
    service = VideoInsightService(_config(), client=client)

    assert service.refine(source) == source


