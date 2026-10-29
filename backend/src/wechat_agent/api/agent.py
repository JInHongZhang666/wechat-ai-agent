"""Opt-in M2 API. Synchronous bounded runs; durable recovery is a later stage."""
from collections import OrderedDict
from datetime import UTC, datetime
from threading import Lock
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from wechat_agent.agent.config import AgentConfig
from wechat_agent.agent.integrations import AihotResearchExtension
from wechat_agent.agent.model import ToolCallingModel
from wechat_agent.agent.tools import ResearchTools
from wechat_agent.api.errors import AppError


class RunRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    goal: str = Field(min_length=2, max_length=2000)
    mode: Literal['query', 'research'] = 'query'
    refresh_sources: bool = Field(default=False, strict=True)


class ConversationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    message: str = Field(min_length=2, max_length=2000)
    mode: Literal['query', 'research'] = 'query'
    refresh_sources: bool = Field(default=False, strict=True)


class MessageRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    message: str = Field(min_length=2, max_length=2000)
    refresh_sources: bool = Field(default=False, strict=True)


def create_agent_router(get_service, *, config=None, model_factory=ToolCallingModel):
    config = config or AgentConfig.from_environment()
    router = APIRouter(prefix='/api/v1/agent', tags=['Agent M2'])
    runs = OrderedDict()
    conversations = OrderedDict()
    run_lock, cache_lock = Lock(), Lock()

    def build_tools(service, *, refresh_sources=False):
        extensions = []
        if config.aihot_enabled:
            extensions.append(AihotResearchExtension(timeout_seconds=config.aihot_timeout_seconds))
        return ResearchTools(service, allow_refresh=refresh_sources, extensions=extensions)

    def require_runtime():
        if not config.enabled:
            raise AppError(503, 'AGENT_DISABLED', 'Agent试运行未启用。')
        service = get_service()
        if not service._llm_config:
            raise AppError(503, 'AGENT_MODEL_NOT_CONFIGURED', '请配置支持工具调用的模型。')
        try:
            from wechat_agent.agent.graph import run_agent
        except ImportError:
            raise AppError(503, 'AGENT_DEPENDENCY_MISSING', '请安装项目agent可选依赖。') from None
        return service, run_agent

    def remember_run(result):
        with cache_lock:
            runs[result['run_id']] = result
            while len(runs) > 100:
                runs.popitem(last=False)

    def execute_turn(message, *, mode, refresh_sources, history=None, conversation_id=None):
        service, run_agent = require_runtime()
        if not run_lock.acquire(blocking=False):
            raise AppError(409, 'AGENT_BUSY', '已有Agent任务正在执行。', retryable=True)
        model = None
        research_tools = None
        try:
            model = model_factory(service._llm_config, config)
            research_tools = build_tools(service, refresh_sources=refresh_sources)
            result = run_agent(
                message, model,
                research_tools,
                config, mode=mode, history=history,
                conversation_id=conversation_id,
            )
            remember_run(result)
            return result
        finally:
            try:
                if model is not None:
                    model.close()
            finally:
                try:
                    if research_tools is not None:
                        research_tools.close()
                finally:
                    run_lock.release()

    @router.get('/status')
    def status():
        configured = bool(get_service()._llm_config) if config.enabled else False
        tools = build_tools(get_service())
        tool_names = list(tools.definitions)
        tools.close()
        return {'stage': 'M2', 'enabled': config.enabled, 'model_configured': configured,
                'tools': tool_names, 'read_only_by_default': True,
                'modes': ['query', 'research'], 'refresh_requires_opt_in': True,
                'aihot_enabled': config.aihot_enabled,
                'execution_mode': 'synchronous', 'storage': 'process_memory',
                'retained_runs': 100, 'resume_supported': False,
                'conversations_supported': True, 'conversation_storage': 'process_memory',
                'conversation_resume_after_restart': False,
                'max_steps': config.max_steps, 'max_tool_calls': config.max_tool_calls}

    @router.post('/runs', status_code=201)
    def create_run(request: RunRequest):
        return execute_turn(request.goal, mode=request.mode,
                            refresh_sources=request.refresh_sources)

    @router.post('/conversations', status_code=201)
    def create_conversation(request: ConversationRequest):
        # Validate the runtime before reserving a conversation id.
        require_runtime()
        conversation_id = str(uuid4())
        created_at = datetime.now(UTC).isoformat()
        conversation = {'conversation_id': conversation_id, 'mode': request.mode,
                        'status': 'running', 'created_at': created_at,
                        'updated_at': created_at, 'messages': [], 'run_ids': []}
        result = execute_turn(
            request.message, mode=request.mode, refresh_sources=request.refresh_sources,
            history=[], conversation_id=conversation_id,
        )
        conversation['messages'].extend([
            {'role': 'user', 'content': request.message},
            *([{'role': 'assistant', 'content': result['answer']}]
              if result.get('answer') else []),
        ])
        conversation['status'] = result['status']
        conversation['updated_at'] = datetime.now(UTC).isoformat()
        conversation['run_ids'].append(result['run_id'])
        with cache_lock:
            conversations[conversation_id] = conversation
            while len(conversations) > 100:
                conversations.popitem(last=False)
        return {**conversation, 'last_run': result}

    @router.post('/conversations/{conversation_id}/messages')
    def send_message(conversation_id: str, request: MessageRequest):
        with cache_lock:
            conversation = conversations.get(conversation_id)
            if conversation is not None:
                history = [dict(message) for message in conversation['messages']]
                mode = conversation['mode']
        if conversation is None:
            raise AppError(404, 'AGENT_CONVERSATION_NOT_FOUND',
                           '会话不存在或已随服务重启清空。')
        result = execute_turn(
            request.message, mode=mode, refresh_sources=request.refresh_sources,
            history=history, conversation_id=conversation_id,
        )
        with cache_lock:
            conversation = conversations.get(conversation_id)
            if conversation is None:
                raise AppError(404, 'AGENT_CONVERSATION_NOT_FOUND',
                               '会话不存在或已随服务重启清空。')
            conversation['messages'].append({'role': 'user', 'content': request.message})
            if result.get('answer'):
                conversation['messages'].append({'role': 'assistant', 'content': result['answer']})
            conversation['status'] = result['status']
            conversation['updated_at'] = datetime.now(UTC).isoformat()
            conversation['run_ids'].append(result['run_id'])
            conversations.move_to_end(conversation_id)
            payload = {**conversation, 'messages': [dict(m) for m in conversation['messages']],
                       'run_ids': list(conversation['run_ids']), 'last_run': result}
        return payload

    @router.get('/conversations/{conversation_id}')
    def get_conversation(conversation_id: str):
        with cache_lock:
            conversation = conversations.get(conversation_id)
            if conversation is not None:
                return {**conversation,
                        'messages': [dict(message) for message in conversation['messages']],
                        'run_ids': list(conversation['run_ids'])}
        raise AppError(404, 'AGENT_CONVERSATION_NOT_FOUND',
                       '会话不存在或已随服务重启清空。')

    @router.get('/runs/{run_id}')
    def get_run(run_id: str):
        with cache_lock:
            result = runs.get(run_id)
        if result is None:
            raise AppError(404, 'AGENT_RUN_NOT_FOUND', '任务记录不存在或已随服务重启清空。')
        return result

    return router
