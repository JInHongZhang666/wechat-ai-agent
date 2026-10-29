from threading import RLock
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from wechat_agent.agent.config import AgentConfig
from wechat_agent.api.agent import create_agent_router
from wechat_agent.api.errors import AppError, app_error_handler
from wechat_agent.config import LlmConfig
from wechat_agent.domain.models import AccountProfile
from wechat_agent.main import create_app


def test_default_app_keeps_agent_disabled(monkeypatch):
    monkeypatch.delenv('WECHAT_AGENT_AGENT_ENABLED', raising=False)
    app = create_app(enable_default_live_collector=False, load_llm_from_environment=False)
    with TestClient(app) as client:
        assert client.get('/api/v1/agent/status').json()['enabled'] is False
        response = client.post('/api/v1/agent/runs', json={'goal':'查询AI资讯'})
        assert response.status_code == 503 and response.json()['code'] == 'AGENT_DISABLED'


def test_enabled_without_model_returns_configuration_error():
    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(create_agent_router(lambda: SimpleNamespace(_llm_config=None),
                                         config=AgentConfig(enabled=True)))
    client = TestClient(app)
    assert client.post('/api/v1/agent/runs', json={'goal':'查询资讯'}).json()['code'] == 'AGENT_MODEL_NOT_CONFIGURED'


def test_api_runs_real_sqlite_query_and_keeps_bounded_run_result(tmp_path):
    pytest.importorskip('langgraph')
    from wechat_agent.storage.sqlite import SqliteArticleRepository, SqliteCurationDecisionRepository
    from wechat_agent.services.pipeline import ContentPipeline

    repo = SqliteArticleRepository(f"sqlite:///{tmp_path / 'agent.db'}")
    service = SimpleNamespace(_llm_config=LlmConfig(api_key='test-only'),
        pipeline=ContentPipeline(repo, curation_repository=SqliteCurationDecisionRepository(repo.engine)),
        account_profile=AccountProfile(), lock=RLock())

    class Model:
        closed = False
        def __init__(self, *args): self.n = 0
        def decide(self, messages, tools, remaining_seconds, *, require_search=False):
            self.n += 1
            if self.n == 1:
                assert require_search is True
                return {'role':'assistant','content':None,'tool_calls':[
                    {'id':'c1','type':'function','function':{'name':'search_ai_news','arguments':'{}'}}]}, 5
            assert 'local_collected_articles' in messages[-1]['content']
            return {'role':'assistant','content':'本地暂无符合范围的资讯。','tool_calls':[]}, 5
        def close(self): Model.closed = True

    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(create_agent_router(lambda: service, config=AgentConfig(enabled=True), model_factory=Model))
    client = TestClient(app)
    response = client.post('/api/v1/agent/runs', json={'goal':'查询最近AI资讯'})
    assert response.status_code == 201
    result = response.json()
    assert result['status'] == 'completed' and result['tool_calls'] == 1
    assert result['observations'][0]['result']['data']['matched_count'] == 0
    assert client.get('/api/v1/agent/runs/'+result['run_id']).json() == result
    assert client.get('/api/v1/agent/runs/missing').status_code == 404
    assert Model.closed
    assert client.post('/api/v1/agent/runs', json={'goal':'  '}).status_code == 422
    assert client.get('/api/v1/agent/status').json()['resume_supported'] is False


def test_conversation_searches_immediately_then_refines_from_prior_messages(tmp_path):
    pytest.importorskip('langgraph')
    from wechat_agent.storage.sqlite import SqliteArticleRepository, SqliteCurationDecisionRepository
    from wechat_agent.services.pipeline import ContentPipeline

    repo = SqliteArticleRepository(f"sqlite:///{tmp_path / 'conversation.db'}")
    service = SimpleNamespace(_llm_config=LlmConfig(api_key='test-only'),
        pipeline=ContentPipeline(repo, curation_repository=SqliteCurationDecisionRepository(repo.engine)))

    class Model:
        histories = []
        def __init__(self, *args):
            self.n = 0
        def decide(self, messages, tools, remaining_seconds, *, require_search=False):
            Model.histories.append(list(messages))
            self.n += 1
            if self.n == 1:
                assert require_search is True
                return {'role':'assistant','content':None,'tool_calls':[
                    {'id':'c1','type':'function','function':{
                        'name':'search_ai_news','arguments':'{"query":"OpenAI","window":"7d"}'}}]}, 5
            assert require_search is False
            return {'role':'assistant','content':'本地暂无匹配资讯。','tool_calls':[]}, 5
        def close(self): pass

    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(create_agent_router(lambda: service, config=AgentConfig(enabled=True),
                                           model_factory=Model))
    client = TestClient(app)
    first = client.post('/api/v1/agent/conversations', json={'message':'最新OpenAI资讯'})
    assert first.status_code == 201
    first_data = first.json()
    assert first_data['status'] == 'completed'
    assert first_data['last_run']['tool_calls'] == 1
    conversation_id = first_data['conversation_id']

    second = client.post(f'/api/v1/agent/conversations/{conversation_id}/messages',
                         json={'message':'只看产品更新'})
    assert second.status_code == 200
    second_data = second.json()
    assert second_data['status'] == 'completed'
    assert [item['role'] for item in second_data['messages']] == [
        'user', 'assistant', 'user', 'assistant']
    assert Model.histories[2][-3]['content'] == '最新OpenAI资讯'
    assert Model.histories[2][-2]['role'] == 'assistant'
    assert Model.histories[2][-1]['content'] == '只看产品更新'
    stored = client.get(f'/api/v1/agent/conversations/{conversation_id}').json()
    assert stored['run_ids'] == second_data['run_ids']
    status = client.get('/api/v1/agent/status').json()
    assert status['conversations_supported'] is True
    assert status['aihot_enabled'] is True
    assert 'search_aihot_news' in status['tools']
    assert 'get_account_profile' not in status['tools']


def test_aihot_tool_can_be_removed_with_configuration():
    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)
    service = SimpleNamespace(_llm_config=LlmConfig(api_key='test-only'))
    app.include_router(create_agent_router(
        lambda: service,
        config=AgentConfig(enabled=True, aihot_enabled=False),
    ))
    status = TestClient(app).get('/api/v1/agent/status').json()
    assert status['aihot_enabled'] is False
    assert 'search_aihot_news' not in status['tools']
