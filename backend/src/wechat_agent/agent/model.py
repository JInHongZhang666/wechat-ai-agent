"""Provider-compatible native function calling, with no rule-based impersonation."""

from openai import OpenAI

from wechat_agent.writing.provider import classify_llm_error


class ModelFailure(Exception):
    pass


class ToolCallingModel:
    def __init__(self, llm_config, config):
        self.config = config
        self.model = config.model or llm_config.model
        self.client = OpenAI(api_key=llm_config.api_key, base_url=llm_config.base_url,
                             max_retries=0, timeout=min(llm_config.request_timeout, config.timeout_seconds))

    def decide(self, messages, tools, remaining_seconds, *, require_search=False):
        try:
            request_messages = list(messages)
            if require_search:
                request_messages.append({'role': 'system', 'content':
                    '本轮必须立即调用可用的资讯搜索工具。最新且不超过7天的需求优先使用search_aihot_news；'
                    '需要本地库或更早范围时使用search_ai_news。信息不完整时采用最近7天和最佳努力关键词，'
                    '不得向用户提问；用户稍后可以继续修正条件。'})
            response = self.client.chat.completions.create(
                model=self.model, messages=request_messages, tools=tools, tool_choice='auto',
                max_tokens=self.config.max_output_tokens,
                timeout=max(0.1, min(remaining_seconds, 60)),
            )
            choice = response.choices[0]
            if choice.finish_reason not in {'stop', 'tool_calls'}:
                raise ModelFailure('MODEL_INCOMPLETE_RESPONSE')
            message = choice.message
            # Do not expose provider-specific reasoning fields.
            return {
                'role': 'assistant', 'content': message.content,
                'tool_calls': [
                    {'id': c.id, 'type': 'function', 'function': {
                        'name': c.function.name, 'arguments': c.function.arguments}}
                    for c in (message.tool_calls or [])
                ],
            }, (response.usage.total_tokens if response.usage else None)
        except ModelFailure:
            raise
        except Exception as exc:
            raise ModelFailure(classify_llm_error(exc)) from None

    def close(self):
        self.client.close()
