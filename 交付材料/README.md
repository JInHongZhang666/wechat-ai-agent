# AI 信息小屋 Agent 交付物清单

本目录对应实训要求中的七项交付物。所有说明均以当前仓库代码和 2026 年 10 月 29 日的测试结果为准。

| 序号 | 交付物 | 文件或位置 | 说明 |
|---|---|---|---|
| 1 | Agent 架构设计图 | `01-Agent架构设计图.png` | 展示 Thought、Action、Observation、Answer 循环，以及工具与会话记忆的关系。 |
| 2 | 工具接口说明 | `02-工具接口说明.md` | 记录 5 个工具的用途、参数约束、返回格式、错误码和调用示例。 |
| 3 | 核心源码 | 项目根目录 | 完整可运行工程；新增根目录 `requirements.txt`、`Dockerfile` 和 `.dockerignore`，已有 `.env.example`。 |
| 4 | Prompt 设计文档 | `04-Prompt设计文档.md` | 记录 System Prompt 的职责、强制搜索策略、安全边界、迭代原因和关键模板。 |
| 5 | 评测记录 | `05-Agent评测记录.md`、`05-Agent评测记录.json` | 含 12 条代表性用例及完整测试结果：Agent 专项 39 项通过，项目全量 421 项通过。 |
| 6 | 部署说明 | `06-部署说明.md` | 包含本地 PowerShell、Docker、环境变量、健康检查、视频服务和常见故障。 |
| 7 | 实训报告 | `07-AI信息小屋Agent实训报告.docx` | 按实训报告模板编排，覆盖项目基本信息、需求、架构、工具、Prompt、难点、评测和总结。 |

## 验证结论

- Agent 的运行循环由 LangGraph `StateGraph` 实现，实际执行 `decide -> execute -> observe -> decide`，而不是一次普通 API 调用。
- Query 模式必须先执行资讯搜索；Research 模式还必须建立证据包，证据不足时返回 `needs_evidence`。
- 工具参数由 Pydantic 严格校验，未知字段、错误类型、非法枚举和越界数值不会进入业务服务。
- Agent 默认只读。刷新信源必须由 API 请求显式授权，模型不能自行扩大权限。
- AIHOT 以独立扩展接入，可通过 `WECHAT_AGENT_AIHOT_ENABLED=0` 关闭，并可删除单独适配器移除。
- 当前会话和运行记录保存在进程内，最多保留 100 份；服务重启后不会恢复，这是当前版本的明确限制。

