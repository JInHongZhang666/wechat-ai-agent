# AI 信息小屋 Agent 评测记录

## 1 评测环境

- 日期：2026 年 10 月 29 日
- Python：3.13.0，项目要求 3.12 及以上
- FastAPI：0.141.1
- Pydantic：2.13.4
- SQLAlchemy：2.0.51
- httpx：0.28.1
- OpenAI SDK：1.109.1
- LangGraph：1.x
- Uvicorn：0.52.0

测试使用脚本模型和临时 SQLite，避免为分支测试调用付费模型。这样可以固定每一步决策，精确验证循环、权限和异常处理。

## 2 代表性测试用例

| 编号 | 输入或场景 | 期望行为 | 实际结果 | 结论 |
|---|---|---|---|---|
| T01 | “帮我找资讯”，未给时间 | 立即按 7 天窗口搜索，不先追问 | 调用 `search_ai_news`，window 为 7d | 通过 |
| T02 | 24 小时搜索为空 | 模型读取空 Observation 后改用 7 天重查 | 3 个步骤、2 次工具调用后完成 | 通过 |
| T03 | 模型先回复“关注哪个主题” | 不向用户展示澄清，内部要求直接搜索 | 生成 `search_decision_retried` 事件并完成搜索 | 通过 |
| T04 | 后续消息“只看产品更新” | 保留上一轮 OpenAI 主题并重新查询 | 完整历史传给模型，会话消息顺序正确 | 通过 |
| T05 | `limit=999` 或未知字段 | 参数校验失败，不能调用仓储 | 返回 `INVALID_TOOL_ARGUMENTS` | 通过 |
| T06 | 调用未注册的 `export_article` | 阻止越权工具 | 返回 `UNKNOWN_TOOL` | 通过 |
| T07 | 模型生成错误 JSON | 把错误作为 Observation，允许修正 | 首次返回 `INVALID_TOOL_JSON`，第二次修正后完成 | 通过 |
| T08 | 模型鉴权失败 | 不能伪装成功 | status 为 failed，error 为 `LLM_AUTH_FAILED` | 通过 |
| T09 | 超过最大步骤 | 停止循环 | 返回 `STEP_LIMIT` | 通过 |
| T10 | 超过最大工具调用 | 在额外执行前停止 | 返回 `TOOL_CALL_LIMIT`，只执行已允许的调用 | 通过 |
| T11 | 单一来源构建证据 | 标出独立来源不足 | `evidence_ready=false`，包含 `INSUFFICIENT_INDEPENDENT_SOURCES` | 通过 |
| T12 | 两个来源数值冲突 | Research 模式不能声称核验完成 | 返回 `needs_evidence`，回答说明存在事实冲突 | 通过 |
| T13 | 未授权刷新信源 | 不执行带副作用操作 | 返回 `REFRESH_NOT_ALLOWED`，采集方法未被调用 | 通过 |
| T14 | 同一轮重复刷新 | 只执行一次并复用结果 | 第二次结果带 `reused=true` | 通过 |
| T15 | 两次 Agent 运行共享文章 ID | 不允许跨运行复用观察状态 | 第二轮返回 `ARTICLE_NOT_OBSERVED` | 通过 |
| T16 | API 启用但未配置模型 | 返回明确配置错误 | HTTP 503，`AGENT_MODEL_NOT_CONFIGURED` | 通过 |
| T17 | AIHOT 配置关闭 | 工具列表不出现 AIHOT | `aihot_enabled=false` 且无 `search_aihot_news` | 通过 |

## 3 自动化测试结果

```text
Agent 专项：39 passed in 9.01s
项目全量：421 passed in 147.38s
```

两条测试命令均以退出码 0 完成：

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests\unit\test_agent.py tests\integration\test_agent_api.py -q
.\.venv\Scripts\python.exe -m pytest -q
```

## 4 成功与失败分析

本轮自动化测试没有失败用例。评测仍覆盖了预期失败路径，包括非法参数、未知工具、模型鉴权失败、步骤上限、工具上限、超时、证据不足、来源冲突、刷新未授权和模型未配置。系统把这些情况转换为稳定状态和错误码，而不是抛出未处理异常。

当前评测没有把外部网络稳定性计入通过条件。AIHOT、GitHub、RSS 或模型服务商可能因为限频、余额、网络或上游格式变化而失败。代码已对这些情况设置错误码和重试边界，但部署后还应增加定时真实链路检查。

## 5 当前局限

- Agent 会话和运行记录只保存在进程内，服务重启会清空。
- Agent 同步执行，并使用非阻塞单运行锁；并发请求会收到 `AGENT_BUSY`。
- 证据构建基于采集摘要的确定性匹配，不等同于对原文逐段核验。
- AIHOT 是外部发现来源，不参与本地证据门。
- 自动化测试验证行为和接口，不验证付费模型在所有主题上的语言质量。

