"""Run a bounded local query or M2 evidence task from the backend directory."""
import argparse
import json
import os
from threading import RLock
from types import SimpleNamespace

from .config import AgentConfig
from .model import ToolCallingModel
from .tools import ResearchTools
from wechat_agent.config import load_llm_config
from wechat_agent.domain.models import AccountProfile
from wechat_agent.services.pipeline import ContentPipeline
from wechat_agent.storage.sqlite import (
    SqliteArticleRepository, SqliteCurationDecisionRepository, SqliteAccountProfileRepository,
)


def main():
    parser = argparse.ArgumentParser(description='M2研究Agent，读取本地资讯库并检查摘要证据。')
    parser.add_argument('--goal', required=True)
    parser.add_argument('--mode', choices=['query', 'research'], default='query')
    parser.add_argument('--database-url', default=os.getenv('WECHAT_AGENT_DATABASE_URL', 'sqlite:///data/wechat_agent.db'))
    args = parser.parse_args()
    config = AgentConfig.from_environment()
    if not config.enabled:
        parser.exit(2, 'AGENT_DISABLED: set WECHAT_AGENT_AGENT_ENABLED=1\n')
    if not 2 <= len(args.goal.strip()) <= 2000:
        parser.exit(2, 'INVALID_GOAL: goal must be 2..2000 characters\n')
    llm = load_llm_config()
    if llm is None:
        parser.exit(2, 'AGENT_MODEL_NOT_CONFIGURED\n')
    if not args.database_url.startswith('sqlite:'):
        parser.exit(2, 'M1 CLI requires SQLite; use the app API for other storage.\n')
    from .graph import run_agent
    repo = SqliteArticleRepository(args.database_url)
    service = SimpleNamespace(
        pipeline=ContentPipeline(repo, curation_repository=SqliteCurationDecisionRepository(repo.engine)),
        account_profile=SqliteAccountProfileRepository(repo.engine).get_current() or AccountProfile(),
        lock=RLock(),
    )
    model = ToolCallingModel(llm, config)
    try:
        result = run_agent(args.goal.strip(), model, ResearchTools(service), config, mode=args.mode)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        model.close()
        repo.engine.dispose()
    return 0 if result['status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
