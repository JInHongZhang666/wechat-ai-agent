"""Agent branching is tested with scripted decisions, not paid model calls."""
import json
from datetime import UTC, datetime, timedelta
from threading import RLock
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip('langgraph')

from wechat_agent.agent.config import AgentConfig
from wechat_agent.agent.graph import run_agent
from wechat_agent.agent.model import ModelFailure, ToolCallingModel
from wechat_agent.agent.tools import ResearchTools
from wechat_agent.config import LlmConfig
from wechat_agent.domain.models import AccountProfile, ArticleRecord, TopicCategory


def call(query='', *, window='7d', name='search_ai_news', raw=None):
    return {'role': 'assistant', 'content': None, 'tool_calls': [
        {'id': 'call1', 'type': 'function', 'function': {'name': name,
         'arguments': raw if raw is not None else json.dumps({'query': query, 'window': window})}}]}


def final(text='查询完成。'):
    return {'role': 'assistant', 'content': text, 'tool_calls': []}


class ScriptedModel:
    def __init__(self, *decisions):
        self.decisions = iter(decisions)
        self.histories = []
        self.require_search = []

    def decide(self, messages, tools, remaining_seconds, *, require_search=False):
        self.histories.append(list(messages))
        self.require_search.append(require_search)
        result = next(self.decisions)
        if isinstance(result, Exception):
            raise result
        if callable(result):
            result = result(messages)
        return result, 12

    def close(self):
        pass


@pytest.fixture
def service():
    now = datetime.now(UTC)
    article = ArticleRecord(id='a1', title='OpenAI 测试资讯', summary='测试素材，不是真实新闻',
        source_name='test', source_url='https://example.com/news', published_at=now-timedelta(days=2),
        collected_at=now, category=TopicCategory.AI_MODELS)
    pipeline = SimpleNamespace(list_distribution_items=Mock(return_value=[
        (article, SimpleNamespace(eligible=True))]))
    return SimpleNamespace(pipeline=pipeline, account_profile=AccountProfile(), lock=RLock(),
                           _llm_config=LlmConfig(api_key='test-only'))


def test_model_replans_after_real_empty_observation(service):
    def observe(messages):
        result = json.loads(messages[-1]['content'])
        assert result['ok'] and result['data']['matched_count'] == 0
        return call('OpenAI', window='7d')
    model = ScriptedModel(call('OpenAI', window='24h'), observe, final())
    result = run_agent('查询最近AI新闻', model, ResearchTools(service), AgentConfig())
    assert result['status'] == 'completed'
    assert result['steps'] == 3 and result['tool_calls'] == 2
    assert result['observations'][1]['result']['data']['items'][0]['id'] == 'a1'
    assert result['tokens'] == 36


@pytest.mark.parametrize('query,count', [('OpenAI', 1), ('不存在', 0), ('openai', 1)])
def test_search_matches_real_repository_results(service, query, count):
    result = ResearchTools(service).execute('search_ai_news', {'query': query})
    assert result['data']['matched_count'] == count
    assert result['data']['scope'] == 'local_collected_articles'


@pytest.mark.parametrize('arguments', [{'limit': 999}, {'window':'all'}, {'limit':'5'},
                                       {'secret': 'bad'}, [], None])
def test_invalid_arguments_do_not_reach_service(service, arguments):
    result = ResearchTools(service).execute('search_ai_news', arguments)
    assert result['error_code'] == 'INVALID_TOOL_ARGUMENTS'
    service.pipeline.list_distribution_items.assert_not_called()


def test_unknown_tool_cannot_execute(service):
    assert ResearchTools(service).execute('export_article', {})['error_code'] == 'UNKNOWN_TOOL'


def test_clarification_text_is_hidden_and_search_is_retried(service):
    model = ScriptedModel(final('你想关注哪个主题？'), call(), final('已完成查询。'))
    result = run_agent('查询AI新闻', model, ResearchTools(service), AgentConfig())
    assert result['status'] == 'completed'
    assert result['tool_calls'] == 1
    assert {'type': 'search_decision_retried'} in result['events']


def test_broad_conversation_request_searches_immediately_with_default_window(service):
    model = ScriptedModel(call('', window='7d'), final('已按最近7天查询。'))
    result = run_agent('帮我找资讯', model, ResearchTools(service), AgentConfig(),
                       conversation_id='conversation-1')
    assert result['status'] == 'completed'
    assert result['tool_calls'] == 1
    assert result['observations'][0]['result']['data']['window'] == '7d'
    assert result['events'][0] == {'type': 'direct_search_started', 'default_window': '7d'}
    assert model.require_search == [True, False]
    assert result['conversation_id'] == 'conversation-1'


def test_conversation_history_is_passed_to_model(service):
    def inspect(messages):
        assert messages[-3:] == [
            {'role': 'user', 'content': '查询OpenAI资讯'},
            {'role': 'assistant', 'content': '已整理最近7天的OpenAI资讯。'},
            {'role': 'user', 'content': '只看产品更新'},
        ]
        return call('OpenAI', window='7d')
    history = [
        {'role': 'user', 'content': '查询OpenAI资讯'},
        {'role': 'assistant', 'content': '已整理最近7天的OpenAI资讯。'},
    ]
    result = run_agent('只看产品更新', ScriptedModel(inspect, final()),
                       ResearchTools(service), AgentConfig(), history=history)
    assert result['status'] == 'completed'


def test_research_tools_do_not_expose_account_profile(service):
    tools = ResearchTools(service)
    assert 'get_account_profile' not in tools.definitions
    assert tools.execute('get_account_profile', {})['error_code'] == 'UNKNOWN_TOOL'


def test_malformed_json_is_observed_and_can_be_corrected(service):
    result = run_agent('查询AI新闻', ScriptedModel(call(raw='{'), call(), final()),
                       ResearchTools(service), AgentConfig())
    assert result['status'] == 'completed'
    assert result['observations'][0]['result']['error_code'] == 'INVALID_TOOL_JSON'


def test_model_failure_is_not_hidden_as_success(service):
    result = run_agent('查询AI新闻', ScriptedModel(ModelFailure('LLM_AUTH_FAILED')),
                       ResearchTools(service), AgentConfig())
    assert result['status'] == 'failed' and result['answer'] is None
    assert result['error_code'] == 'LLM_AUTH_FAILED'


def test_step_limit_stops_loop(service):
    result = run_agent('查询AI新闻', ScriptedModel(call(), call()), ResearchTools(service),
                       AgentConfig(max_steps=2))
    assert result['error_code'] == 'STEP_LIMIT' and result['tool_calls'] == 2


def test_tool_limit_stops_before_extra_execution(service):
    result = run_agent('查询AI新闻', ScriptedModel(call(), call()), ResearchTools(service),
                       AgentConfig(max_tool_calls=1))
    assert result['error_code'] == 'TOOL_CALL_LIMIT'
    assert result['tool_calls'] == 1


def test_deadline_blocks_late_model_result(service, monkeypatch):
    clock = iter([0, 0, 0, 2])
    monkeypatch.setattr('wechat_agent.agent.graph.monotonic', lambda: next(clock))
    result = run_agent('查询AI新闻', ScriptedModel(call()), ResearchTools(service),
                       AgentConfig(timeout_seconds=1))
    assert result['error_code'] == 'AGENT_TIMEOUT'
    service.pipeline.list_distribution_items.assert_not_called()


def test_storage_errors_are_redacted(service):
    service.pipeline.list_distribution_items.side_effect = RuntimeError('secret-api-key')
    result = ResearchTools(service).execute('search_ai_news', {})
    assert result['error_code'] == 'TOOL_EXECUTION_FAILED'
    assert 'secret-api-key' not in json.dumps(result)


def test_provider_native_tool_message_and_no_reasoning_leak():
    config = AgentConfig()
    provider = ToolCallingModel(LlmConfig(api_key='test'), config)
    provider.client.close()
    provider.client = Mock()
    provider.client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(finish_reason='tool_calls', message=SimpleNamespace(
            content=None, reasoning_content='hidden', tool_calls=[SimpleNamespace(
                id='id1', function=SimpleNamespace(name='search_ai_news', arguments='{}'))]))],
        usage=SimpleNamespace(total_tokens=10))
    message, usage = provider.decide([], [], 5, require_search=True)
    assert message['tool_calls'][0]['function']['name'] == 'search_ai_news'
    assert 'reasoning_content' not in message and usage == 10
    assert provider.client.chat.completions.create.call_args.kwargs['timeout'] == 5
    kwargs = provider.client.chat.completions.create.call_args.kwargs
    assert kwargs['tool_choice'] == 'auto'
    assert '必须立即调用可用的资讯搜索工具' in kwargs['messages'][-1]['content']


def evidence_call(ids):
    return call(name='build_research_evidence', raw=json.dumps({'article_ids': ids}))


def two_sources(service, *, same_publisher=False, conflict=False):
    from wechat_agent.domain.models import SourceType
    article = service.pipeline.list_distribution_items.return_value[0][0]
    first = article.model_copy(update={'summary': 'Acme API supports 10 languages.',
        'source_type': SourceType.OFFICIAL, 'source_type_verified': True,
        'source_identity': 'acme', 'source_verification_basis': 'fixture ownership verification'})
    second = article.model_copy(update={'id':'a2', 'source_url':'https://news.example/acme',
        'published_at': article.published_at - timedelta(minutes=1),
        'summary': 'Acme API supports 20 languages.' if conflict else first.summary,
        'source_identity': 'acme' if same_publisher else 'news.example'})
    service.pipeline.list_distribution_items.return_value = [
        (a, SimpleNamespace(eligible=True)) for a in [first, second]]


def test_evidence_refuses_unobserved_id(service):
    tools = ResearchTools(service)
    assert tools.execute('build_research_evidence', {'article_ids':['a1']})['error_code'] == 'ARTICLE_NOT_OBSERVED'


def test_single_source_has_explicit_gaps(service):
    tools = ResearchTools(service)
    tools.execute('search_ai_news', {})
    result = tools.execute('build_research_evidence', {'article_ids':['a1']})
    assert result['ok'] and not result['data']['evidence_ready']
    assert 'INSUFFICIENT_INDEPENDENT_SOURCES' in result['data']['gaps']
    assert result['data']['scope'] == 'collected_summaries'


def test_same_publisher_cannot_pass_evidence_gate(service):
    two_sources(service, same_publisher=True)
    tools = ResearchTools(service)
    tools.execute('search_ai_news', {})
    data = tools.execute('build_research_evidence', {'article_ids':['a1','a2']})['data']
    assert not data['evidence_ready'] and data['independent_source_count'] == 1


def test_conflicting_numeric_claims_block_research(service):
    two_sources(service, conflict=True)
    result = run_agent('研究这个事件', ScriptedModel(call(), evidence_call(['a1','a2']), final('已经验证')),
                       ResearchTools(service), AgentConfig(), mode='research')
    assert result['status'] == 'needs_evidence'
    assert '存在事实冲突' in result['answer']


def test_research_must_build_evidence_when_candidates_exist(service):
    result = run_agent('研究资讯', ScriptedModel(call(), final()), ResearchTools(service),
                       AgentConfig(), mode='research')
    assert result['error_code'] == 'EVIDENCE_NOT_BUILT'


def test_research_empty_is_needs_evidence_not_completed(service):
    result = run_agent('研究资讯', ScriptedModel(call('missing'), final()), ResearchTools(service),
                       AgentConfig(), mode='research')
    assert result['status'] == 'needs_evidence' and result['error_code'] == 'NO_CANDIDATES'


def test_research_follows_gaps_then_builds_with_supporting_source(service):
    two_sources(service)
    first_query = call(raw=json.dumps({'limit':1}))
    def supplement(messages):
        data = json.loads(messages[-1]['content'])['data']
        assert not data['evidence_ready']
        return call(name='find_supporting_sources', raw=json.dumps({'article_id':'a1','query':'Acme'}))
    result = run_agent('研究Acme事件', ScriptedModel(first_query, evidence_call(['a1']), supplement,
        evidence_call(['a1','a2']), final()), ResearchTools(service), AgentConfig(), mode='research')
    assert result['status'] == 'completed'
    assert result['observations'][-1]['result']['data']['evidence_ready']
    assert result['tool_calls'] == 4


def test_refresh_requires_request_authority(service):
    service.create_scheduled_live_task = Mock()
    result = ResearchTools(service).execute('refresh_news_sources', {})
    assert result['error_code'] == 'REFRESH_NOT_ALLOWED'
    service.create_scheduled_live_task.assert_not_called()


def test_refresh_once_per_run_even_when_repeated(service):
    service.live_collector = object()
    service.create_scheduled_live_task = Mock(return_value=SimpleNamespace(id='task1',collected_article_count=2))
    tools = ResearchTools(service, allow_refresh=True)
    assert tools.execute('refresh_news_sources', {})['data']['task_id'] == 'task1'
    assert tools.execute('refresh_news_sources', {})['reused']
    service.create_scheduled_live_task.assert_called_once()


def test_failed_refresh_is_not_repeated_or_leaked(service):
    service.live_collector = object()
    service.create_scheduled_live_task = Mock(side_effect=RuntimeError('secret'))
    tools = ResearchTools(service, allow_refresh=True)
    assert tools.execute('refresh_news_sources', {})['error_code'] == 'REFRESH_FAILED'
    assert tools.execute('refresh_news_sources', {})['reused']
    service.create_scheduled_live_task.assert_called_once()


def test_run_tools_do_not_share_observed_articles(service):
    first, second = ResearchTools(service), ResearchTools(service)
    first.execute('search_ai_news', {})
    assert second.execute('build_research_evidence', {'article_ids':['a1']})['error_code'] == 'ARTICLE_NOT_OBSERVED'


def test_latest_failed_evidence_cannot_reuse_earlier_success(service):
    two_sources(service)
    result = run_agent('研究资讯', ScriptedModel(call(), evidence_call(['a1','a2']),
        evidence_call(['invented']), final()), ResearchTools(service), AgentConfig(), mode='research')
    assert result['status'] == 'failed' and result['error_code'] == 'LATEST_EVIDENCE_FAILED'
