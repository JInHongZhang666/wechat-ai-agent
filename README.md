# AI 信息小屋 · 公众号智能体

这是一个同时面向信息读者与公众号创作者的本地智能体：共享同一套文章、
评分和证据数据，提供高质量 AI 信息分发与公众号内容生产。当前前端延续
AIHOT 风格，不再采用旧三栏工作台方案。

正式运行界面位于 `backend/src/wechat_agent/web`，其生产构建会输出到同目录
`dist` 并由 FastAPI 直接提供。项目根目录的 `frontend` 是冻结的旧工作台，
只保留兼容构建和契约类型漂移检查；新页面、路由和样式不得继续在两套前端
重复实现。

现有创作主链路已经把
“公开数据自动采集 → 去重与热度评分 → 账号画像 → 内容机会评分 → 采用/忽略
→ 证据包 → Content Brief → 作者材料 → 研究初稿 → 质量检查 → 人工编辑
→ 最终人工确认 → 微信富文本导出”接到一个可操作网页中。SQLite v16 持久化
CreatorProject、版本化账号画像、内容机会、作者材料、草稿版本、质量报告与
导出记录，并新增 PromptVersion、模型调用审计和创作质量回放。AI 生成采用
“大纲 → 逐节成文 → 总编整合”三阶段；未配置模型时明确显示规则回退，不会
伪装成 AI 成稿。Brief 未通过证据门不能生成正式研究稿，质量阻断版本或未经最终
人工确认的版本不能正式导出。可在 `/create/topics` 管理画像与机会，再在
`/create/drafts` 与 `/create/quality` 完成后续步骤。

2026-08-12 起，内容生产主入口升级为“热门文章/用户链接驱动的原创研究链路”：
可以点选 AIHOT 当前高分文章，或粘贴一篇公开文章链接作为主灵感，并追加其他
资料链接。系统解析原文内容与排版结构，但只把“开篇方式、段落节奏、信息组织”
等抽象蓝图交给写作阶段；事实必须来自独立相关资料并保留引用。每个草稿版本都会
对主灵感原文运行 `normalized-char-12gram-v1` 可解释重复率检测，分数超过 10%、
原文未成功解析、事实证据不足或其他质量门未通过时，均禁止定稿和导出。链接导入
仅支持公开 HTTP/HTTPS 页面，并拦截本机、内网、保留网段和重定向绕过。

可靠性基线：SQLite 文件模式会持久化文章、分发决策、任务、选题结果、
日报修订、证据包、草稿及版本历史；v1 API 支持五分类、ETag 和稳定 keyset
分页；
质量阻断项无法绕过导出。正式导出支持 Markdown、HTML、纯文本和可粘贴到
公众号编辑器的内联样式 HTML，并为每次导出保留内容哈希和版本审计记录。

非内容创作板块以根目录 `AIHOT深度分析报告.md` 为强约束。公开机器契约固定在
`GET /openapi-v1.json`，目前只包含已经落地的 AIHOT 匿名只读分发端点；
`openapi-v1.json` 与嵌入式前端类型由
`backend/scripts/generate_public_contract.py` 同源生成并接受漂移测试。公开 v1
错误使用 Problem JSON，响应带 requestId、Server-Timing 和缓存状态；进程内
聚合观测仅由 `/api/ops/http-observability` 提供，不纳入公开 v1。

精选持久镜像使用 AIHOT 双游标协议：首次调用
`GET /api/v1/selected/snapshot?fields=minimal&limit=500`，只把 `nextPage`
用于本轮翻页；全部页面完成后保存整轮恒定的 `cursor`，再调用
`GET /api/v1/selected/changes?cursor=...&limit=100` 应用幂等 upsert/remove。
SQLite v9 与 MongoDB 都持久化变更序列、同步水位和冻结快照；过期或混用游标
返回 `409 SNAPSHOT_REQUIRED`，客户端只允许一次有界全量重建。变更保留量和
快照会话时长可通过 `WECHAT_AGENT_SELECTED_CHANGE_RETENTION`（默认 10000）与
`WECHAT_AGENT_SNAPSHOT_TTL_SECONDS`（默认 900）配置。

SQLite 现在还会持久化 SourceRegistry、真实逐来源 SourceRun 和版本化 schema
migration。每次采集会保存窗口、HTTP 状态、抓取/入库/跳过数、稳定错误码和
耗时，并回写 7 日成功率、连续失败、退避时间和熔断状态。默认 33 个配置中
启用 24 个已验证核心源，9 个无可靠 RSS/API 的来源保留注册信息但明确停用；
种子刷新不会覆盖人工禁用、tier 修订或健康状态。

可通过 `GET /api/v1/sources` 查看信源健康，通过
`GET /api/v1/sources/{sourceId}/runs` 定位单源执行记录。后台每分钟只轮询
到期来源，实际执行由持久化 `nextRunAt`、数据库租约、退避和熔断共同决定；
手动与后台任务复用同一套 single-flight 执行器。429 会遵守 Retry-After，
永久 4xx 直接进入冷却，open circuit 到期后只允许 half-open 探测。Phase 2B
还提供 `GET /api/v1/sources/reliability?days=7`，用真实 SourceRun 显示每个
来源的样本量、成功率、目标值和观察窗口是否完整。200 条人工 gold 与首份
确定性基线已经完成，后续仍需完成连续 7 日可靠性验收。

采集运行时会把“单个来源故障”和“本机网络、DNS 或代理造成的全局传输故障”
分开处理。大面积 `http_timeout`/`network_error` 不再让全部来源同时反复半开；
系统进入共享冷却后，每批最多探测 3 个来源并逐步恢复。内部状态可通过
`GET /api/v1/sources/network-status` 查看，并在创作任务页显示。GitHub Releases
采集器会在配置 `GITHUB_TOKEN` 时自动使用认证额度，未配置时继续使用公开额度。
SQLite v17 还会给已确认的全局断网批次写入独立的
`reliabilityExcludedReason=global_network_outage` 审计标记：原始状态和错误码继续
保留，但这些本机网络故障不再计入各信源自己的七日成功率。迁移会按批次相关性
回填历史记录，不删除 SourceRun，也不重置观察窗口。

日报已经成为正式持久化实体：以 `Asia/Shanghai` 自然日分桶，保存不可变
revision、内容哈希、策略版本和来源快照；重复生成保持幂等，迟到内容产生新
revision。`/api/v1/dailies`、日报页面和 `/feed/daily.xml` 消费同一份结果，
服务每日 08:00 生成前一日版本，重启不会改变内容、顺序或生成时间。

资讯流支持 `by=timeline|published`：timeline 对 72 小时内的新内容使用发现
时间，对历史回填使用原始发布时间；分页游标绑定查询条件、排序方式和评分
策略，翻页期间插入新文章不会挤动已开始的结果集。首页临时热点仍不冒充
Story，只有相同规范化标题得到至少 2 个独立 `publisherGroup` 佐证时才展示；
正式数据没有合格热点时明确返回空列表。

正式资讯页面已经全部接入真实 v1 API：精选、全部动态、日报和五分类不再
使用 Mock。每条公开资讯保留来源等级、四维评分、代码阈值、精选结果、
推荐理由、评分置信度和策略版本；服务重启后不依赖任务内存恢复 Feed。
没有配置 LLM 时会使用保守的离线估算，并在推荐理由中明确标注，不会把
未达到阈值的候选条目显示为精选。

分发链路现在按采集原文指纹做增量处理：原始文章先持久化，未变化内容在
进入预筛、分类和评分模型前直接跳过；预筛拒绝的文章也不会在下一轮重复
消耗模型。跨轮精选会重新执行分类容量平衡，避免旧精选和新精选叠加突破
分类上限。模型调用另有进程内每日预算以及 401/402 熔断，运行状态可从
`GET /api/v1/creation/runtime` 的 `distributionProcessing` 查看。采集器本身
不依赖大模型；`start.ps1` 默认把分发模型每日额度设为 0，采集、去重、入库、
离线评分与页面更新仍会持续运行。

## Agent M2 试运行

M2现以“直接搜索→补充来源→证据检查→回答”为核心，不再向Agent暴露公众号画像。
用户没有写明时间范围时默认最近7天，不以追问阻断检索；后续消息可以继承主题并
修改时间或关注角度。对话上下文当前保存在
进程内存中，服务重启后清空；持久会话属于后续阶段。

M2 在M1查询闭环基础上增加研究模式：先查询真实文章ID，再构建摘要证据包，
根据缺口查询补充来源并重新构建。默认 `mode=query` 保留查询行为；
`mode=research` 则强制检查证据。API请求示例：

```json
{"goal":"选择近期一条AI事件，检查证据并补充来源，缺口无法解决就说明不足", "mode":"research", "refresh_sources":false}
```

当前本地工具为 `search_ai_news`、`find_supporting_sources`、
`build_research_evidence` 和 `refresh_news_sources`。补充来源和证据工具仅使用
本次任务已查询到的文章和本地库；证据
保存在本次运行结果中，未建立可恢复的持久研究项目。补充来源是具体事件的
关键词候选检索，候选数不能替代事实验证。

只有API请求显式设置 `refresh_sources=true` 才能调用刷新工具。刷新复用已有
调度采集服务，只运行已配置且到期的来源，遵守来源租约和熔断；不接受模型
提供的URL，不是全网搜索。单任务内最多尝试一次，即使失败也不会重复写入；
跨HTTP请求的任务去重及持久恢复仍在后续阶段。刷新可能写入采集任务和资讯，
并受原分发模型预算控制。CLI保持本地研究，不启用网络刷新。

AIHOT 以独立可移除扩展接入，提供 `search_aihot_news`，只读取
`https://aihot.news/api/v1/items` 的匿名公开 JSON 数据，用于最近24小时或7天的
外部资讯发现。它不会写入本地库，也不能把外部条目ID交给本地证据工具。设置
`WECHAT_AGENT_AIHOT_ENABLED=0` 可完全关闭并从Agent工具列表移除；请求超时可用
`WECHAT_AGENT_AIHOT_TIMEOUT_SECONDS` 调整。AIHOT内容只作发现线索，回答优先保留
原始来源链接，不能把聚合摘要当成独立事实核验。

AIHOT 还以独立的 `AihotCollector` 接入内容采集管线，用来扩充最近 7 天的候选
资讯。采集结果保留原始媒体名称与原文 URL，仍执行本地 URL 去重、相关性预筛、
四维评分和代码精选门槛；AIHOT 自身分数不会覆盖本地评分，同一 URL 的官方或
直连采集版本优先。该采集器与 Agent 对话工具使用不同开关：

```powershell
# 关闭/开启 AIHOT 候选资讯采集（默认开启）
$env:WECHAT_AGENT_AIHOT_INGEST_ENABLED = "0" # 或 "1"

# all 扩大召回；selected 仅采集 AIHOT 精选，默认 all
$env:WECHAT_AGENT_AIHOT_INGEST_MODE = "all"

# 单次读取 1-100 条，默认 100
$env:WECHAT_AGENT_AIHOT_INGEST_LIMIT = "100"

# 独立请求超时，默认 12 秒
$env:WECHAT_AGENT_AIHOT_INGEST_TIMEOUT_SECONDS = "12"
```

完整移除采集能力时，只需删除
`backend/src/wechat_agent/collectors/aihot.py`，并移除 `collectors/defaults.py`
中的一条 import 和 AIHOT 注册块；原有信源、评分及灵犀探讯 Agent 均不受影响。

研究结果中的 `needs_evidence` 表示正常完成缺口判断，但不能进入正式写作。
服务端检查来源独立性、已验证的一手来源、待核实事实和冲突；模型不能修改
这些结论。未知文章ID、未构建证据或最后一次证据构建失败不能标记完成。
当前证据引擎基于摘要句子匹配，不能替代原文阅读与全面语义核验，可能保守地
保留很多待核实内容。M3将接入Brief、写作和质检，M4实现持久任务恢复。

在下方CLI命令中加 `--mode research` 即可运行研究模式。

M2真实模型冒烟验证：模型执行了查询、证据构建、补充来源检索三个工具调用，
共4轮决策。实际候选只有一个独立来源且包含待核实内容，补源未找到额外候选，
服务端正确返回 `needs_evidence`，没有将它误报为可写作。已验证的是研究与缺口
处理链路；外网刷新适配有自动化测试，本次冒烟未触发真实信源刷新。

### M1 基础与启动方式

2026-10-26 的整改目标为阶段0与阶段1：建立可独立启用的 LangGraph
`decide → execute → decide` 循环，并接入只读资讯查询工具。
模型自行选择工具与参数，读取结果后继续查询或结束。空查询是真实观察结果，
不伪造实时新闻；没有成功执行资讯查询不能标记完成。

今日验收：旧测试回归、工具参数校验、空结果后调整查询、步数与工具次数限制、
超时、模型错误、API集成及一次真实模型验证。研究证据构建、写作和持久恢复
属于后续M2至M4，当前不对这些能力宣称完成。

从项目的 `backend` 目录执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,agent]"
$env:WECHAT_AGENT_AGENT_ENABLED = "1"
# 默认复用已配置的 WECHAT_AGENT_LLM_* 服务商；可单独覆盖Agent模型。
# $env:WECHAT_AGENT_AGENT_MODEL = "服务商支持工具调用的模型ID"
.\.venv\Scripts\python.exe -m wechat_agent.agent --goal "查询本地最近7天的AI资讯，给出来源；没有结果就说明缺口"
```

Web服务须携带上述开关重新启动，才会启用 `POST /api/v1/agent/runs`，
也可在项目目录执行 `./start.ps1 -EnableAgent`（须先安装agent依赖并配置模型）。
请求体为 `{"goal":"查询本地最近7天的AI资讯"}`。
`GET /api/v1/agent/status` 返回能力与开关状态；
`GET /api/v1/agent/runs/{run_id}` 读取最近的结果。

多轮对话使用 `POST /api/v1/agent/conversations`，请求体为
`{"message":"帮我找最近重要的AI资讯","mode":"query"}`。Agent会立即查询；将后续
修正内容发送到
`POST /api/v1/agent/conversations/{conversation_id}/messages`，请求体为
`{"message":"只看OpenAI产品更新"}`。
`GET /api/v1/agent/conversations/{conversation_id}` 可读取当前进程内会话。

M1接口同步返回，单实例同一时间执行一个任务，最多保留100个进程内结果；
重启清空，不支持恢复、取消或流式事件。结果中的 `status` 才是任务结果，
HTTP 201 不代表模型成功。使用现有 `127.0.0.1` 本地部署方式；本阶段接口
没有新增用户认证，不应对公网开放。

默认最多6轮模型决策、8次工具调用、120秒任务时间预算，每次最多1200输出token，
模型网络调用不自动重试。时间预算在节点边界检查，正在执行的数据库查询不强制终止。
`tokens` 为已报告的用量，`usage_complete=false` 表示服务商未提供完整用量。
本阶段尚无精确金额预算；分发侧每日模型限额不约束Agent调用。
可通过 `WECHAT_AGENT_AGENT_MAX_STEPS`、`WECHAT_AGENT_AGENT_MAX_TOOL_CALLS`、
`WECHAT_AGENT_AGENT_TIMEOUT_SECONDS` 调整限制。未配置模型或模型失败会明确报错，
不会使用规则结果冒充Agent成功。安装LangGraph附带LangSmith依赖不代表追踪已接入。

实现参考：[LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)
及[工具调用协议](https://developers.openai.com/api/docs/guides/function-calling)。

2026-10-26 真实模型冒烟验证通过：从现有SQLite读取近7天资讯，模型自行调用
`search_ai_news`，按limit=2返回来源，再生成查询答复；执行2轮模型决策和1次工具调用。
该结果只证明M1查询链路可用，不代表证据核验、写作闭环或长期运行已验收。

## 当前能做什么

- 自动获取最近 7 天的 GitHub 新项目、官方 AI 公告和公开公众号搜索索引。
- 某一类不足 5 项时，只为该类自动扩大到最近 30 天，并报告真实缺口。
- 按 T1/T1.5/T2 信源等级和 authority/novelty/impact/practicality 四维公式
  计算分发分，由代码阈值决定是否精选。
- 在首页查看真实精选和临时热点，在全部动态中搜索、切换 24h/7d 并分页，
  按五分类浏览，同时查询可恢复、可修订的真实日报。
- GitHub 新仓库使用 Star 与 Fork 作为公开代理信号；累计 Star 不会被表述
  为“最近新增 Star”。
- 每次返回最多 5 个 GitHub/实用 AI 工具选题和 5 个 AI 热点时讯选题。
- 为选题生成带来源边的证据包，区分已核实、冲突和待核实信息。
- 用版本化账号画像和七维 creationScore 维护持续刷新的内容机会池。
- 把核验事实转成可编辑 Content Brief，章节显式绑定稳定 claim ID。
- 补充并确认作者真实经验、观点、案例和数据，避免把模型生成内容冒充亲历。
- 生成 5 个标题候选及可编辑研究初稿。
- 检查画像匹配、段落长度、来源、数字、标题、个人体验表述、事实冲突和独立来源数量。
- 保存完整草稿版本与质量报告，人工最终确认后导出公众号富文本。
- 通过网页运行雷达、管理内容机会、查看证据、生成和修改草稿。
- 保留演示数据入口，断网时仍可检查完整流程。

### 当前接入的数据源

| 数据源 | 用途 | 是否需要密钥 | 证据边界 |
| --- | --- | --- | --- |
| GitHub REST Search API | 新发布 AI/LLM/Agent 工具 | 否；可选 Token | Star/Fork 是查询时累计值 |
| OpenAI News RSS | AI 官方时讯 | 否 | 官方一手来源 |
| GitHub AI & ML RSS | 开发者与 AI 时讯 | 否 | 官方一手来源 |
| Hugging Face Blog RSS | 开源模型与工具时讯 | 否 | 官方一手来源 |
| Bing 公开 RSS 索引 | 发现公开公众号文章线索 | 否 | 不是公众号后台数据 |

旧兼容流程在证据不足时仍允许保存研究草稿，但会阻止审核通过和导出；
新的内容生产项目则在 Brief 证据门前直接拒绝生成正式研究稿。工具文章
没有真实试用记录时，不会写成“亲测”或“实测”。

自由编辑后系统不会静默沿用原质量结论：会重新检查数字、来源、个人体验、
冲突、账号画像和结构规则；配置 LLM 时还会执行事实—来源语义对齐。未配置
LLM 时，自由编辑会生成非阻断的人工事实复核审计项，作者必须使用固定确认语
完成最终确认，系统才生成独立的 finalize 版本并允许导出。任何确定性阻断项
仍然不能被人工确认绕过。

任务的 `completed` 表示十个目标主题都至少有一份**当前无阻断、可审核
且可导出**的草稿，而不只是“曾经生成过草稿”。如果编辑使某个主题失去
最后一份合格草稿，任务会从 `completed` 回退到 `review_ready`；恢复
可信正文或为该主题生成另一份合格草稿后，任务才会再次完成。

## 重要边界

微信没有面向第三方开放“搜索全部公众号文章及其真实阅读量”的公共接口。
当前公众号接入是对**已被公开搜索引擎收录的文章做线索发现**，不登录微信、
不绕过验证码、不破解反爬，也不把搜索索引时间伪装成公众号后台发布时间。
所以它无法保证覆盖“所有公众号”。

本阶段仍是本地 MVP，不是完整商业 SaaS，当前**不包含**：

- 微信公众号全量数据库或不可见的真实阅读量；
- 注册、登录、成员和权限管理；
- 订阅、付款和商业化计费；
- 消息通知；
- 自动发布到微信公众号。

`POST /api/tasks/live` 使用真实公开数据源；`POST /api/tasks/demo` 使用
仓库内固定样本，只用于断网演示和回归验证。

## Windows PowerShell 安装与测试

要求 Python 3.12 或更高版本。在仓库根目录打开 PowerShell：

```powershell
cd 05_PROJECTS\wechat-ai-agent\backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -v
```

只运行端到端验收：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\acceptance\test_demo_flow.py -v
```

验收测试使用专用的合成数据。该数据明确标记为 acceptance fixture，
仅用于证明“两个独立发布者 + 一个有验证依据的一手来源”的规则能够贯通；
它不会被当成真实公众号事实或用于生产文章。

### 人工标注与策略回放

当前策略预测只能作为标注建议，不能作为人工真值。可从持久数据库导出待
标注候选：

```powershell
.\.venv\Scripts\python.exe -m wechat_agent.evaluation export-candidates `
  --database-url sqlite:///data/wechat_agent.db `
  --output evaluation/workbench/news-labeling-v1.jsonl `
  --limit 200
```

完成所有人工标签后执行：

```powershell
.\.venv\Scripts\python.exe -m wechat_agent.evaluation validate `
  --input evaluation/gold/news-gold-v1.jsonl

.\.venv\Scripts\python.exe -m wechat_agent.evaluation replay `
  --input evaluation/gold/news-gold-v1.jsonl `
  --output evaluation/reports/distribution-v3.json

.\.venv\Scripts\python.exe -m wechat_agent.evaluation release-gate `
  --database-url sqlite:///data/wechat_agent.db `
  --gold evaluation/gold/news-gold-v1.jsonl `
  --output evaluation/reports/r1-evidence-current.json `
  --report-only
```

完整字段和标注规则见 `backend/evaluation/README.md`。服务启动后可打开
`/ops/evaluation` 使用内部人工标注工作台。2026-08-03 已完成 200/200 条真实
人工标签，Gold 子门、严格校验和首份确定性基线均已通过。当前正式 R1 总闸门
仍为 `blocked`：24/24 个启用源已有样本，22/24 暂达成功率目标，最短观测
1.482/7 天；量子位为 40/45（88.89%），Hacker News AI 为 16/23（69.57%）。
修复版本已真实采集成功，但滚动窗口仍保留旧失败记录。HN 现使用官方 Firebase
API，不再依赖持续 502 的 hnrss 代理。40 条精选 Reviewer A 复核已经完成并固化
为 `news-gold-v1.1.jsonl`；SQLite v12 在保留这些记录的基础上增加另一位真人的
盲审、冲突裁决和 Gold v1.2 发布门禁。第二复核人看不到首轮/A/当前 Gold/策略
答案，全部 40 条完成前冲突区保持锁定。本批 `story_key` 全空且精选/相关标签
完全重合，不能直接作为 Story 聚类 Gold。R2 文章对口径见
`backend/evaluation/STORY-ANNOTATION-GUIDE.md`，先做 50 对双人试标，再扩到 500 对。

### Story 影子模式（story-shadow-v1）

2026-08-27 已完成确定性、零 Token 的 Story 影子引擎：7 天语料窗口、24 小时
候选召回、硬负例优先规则、约束安全聚类、48 小时 settled、增量指纹、稳定
`st_<uuid4>` 公开标识、revision、合并/拆分谱系、SQLite v18 持久化和失败隔离。
Story 在正式文章、精选决策、SourceRun、日报和任务持久化之后运行；失败只记录内部
run，不回滚采集，也不改变精选、热榜、日报、RSS 或创作机会。

内部诊断和有界回放：

```powershell
Invoke-RestMethod http://127.0.0.1:8010/api/ops/story-shadow/status
Invoke-RestMethod "http://127.0.0.1:8010/api/ops/story-shadow/stories?limit=20"

cd backend
.\.venv\Scripts\python.exe -m wechat_agent.story replay `
  --database-url sqlite:///./data/wechat_agent.db --days 7

.\.venv\Scripts\python.exe -m wechat_agent.story export-pairs `
  --database-url sqlite:///./data/wechat_agent.db `
  --output evaluation/workbench/story-pairs-shadow-v1.jsonl --limit 50
```

当前代码门禁为 Story 专项 54 项、后端全量 312 项通过，前端 TypeScript 与 Vite
生产构建通过。`export-pairs` 默认拒绝覆盖已有文件，并输出精确 SHA-256；标注文件
不含算法分数、建议、否决理由、簇 ID 或系统答案。评估顺序保持 `50 → 200 → 500`
对；达到 R2 Gold 的 pair/cluster 指标前，公开 Story、热榜、日报和创作机会切换仍
保持阻断。正式 SQLite v18 迁移必须先完成带时间戳备份和前后 `quick_check=ok`。

## 启动智能体

### 最简单方式

依赖安装完成后，双击项目根目录的 `启动公众号助手.cmd`。命令窗口保持
打开即代表服务正在运行；在浏览器打开：

- 操作页面：`http://127.0.0.1:8010/`
- 接口文档：`http://127.0.0.1:8010/docs`

停止时回到命令窗口按 `Ctrl+C`。

### PowerShell 方式

启动脚本默认使用本地 SQLite 文件持久化任务、证据包和草稿版本。当前
服务的调度器和内存状态仍要求使用一个 Uvicorn worker：

```powershell
cd 05_PROJECTS\wechat-ai-agent
.\start.ps1
```

视频解析使用独立的本地 Speaches 容器，主项目不内嵌第三方源码。首次使用需先
启动 Docker Desktop；脚本会自动选择 NVIDIA GPU 版本，也可用 `-VideoCpu`
强制使用 CPU：

```powershell
# 同时启动资讯 Agent、视频转写服务和主项目
.\start.ps1 -EnableAgent -EnableVideo

# 仅启动/停止独立视频转写服务
.\start-video.ps1
.\start-video.ps1 -Cpu
.\start-video.ps1 -Stop
```

启动后在 `http://127.0.0.1:8010/create/video` 上传本地视频或粘贴公开视频链接。
系统会在本地提取音轨、生成带时间轴的字幕，并支持导出 TXT、SRT、VTT。首次
转写还会下载 faster-whisper 模型；任务数据默认保存在
`backend/data/video_tasks`。如需完全移除，只需删除
`services/video-transcriber`、`start-video.ps1` 及主项目中的视频路由即可，其他
资讯与创作模块不受影响。

开发时如需自动重载，可以直接调用 Uvicorn。请先固定当前项目源码路径，
避免系统中另一份可编辑安装被意外加载；不要把 `--reload` 与多 worker
配合使用：

```powershell
$env:PYTHONPATH = (Resolve-Path .\backend\src)
cd backend
$env:WECHAT_AGENT_DATABASE_URL = "sqlite:///./data/wechat_agent.db"
.\.venv\Scripts\python.exe -m uvicorn wechat_agent.main:app --reload --port 8010
```

启动后优先访问 `http://127.0.0.1:8010/` 使用可视化操作页。

### 可选配置

#### 大模型与成本控制

公开信息采集不需要大模型密钥。默认启动方式会保持分发侧模型调用为 0，使用
确定性预筛、分类与评分回退。如果需要启用 AI 写作或受控开启分发模型，请只在
本机 PowerShell 环境变量中配置，不要把 Key 写入仓库、前端代码或聊天记录：

```powershell
$env:WECHAT_AGENT_LLM_API_KEY = "新建的受限 API Key"
$env:WECHAT_AGENT_LLM_BASE_URL = "https://你的服务商/v1"
$env:WECHAT_AGENT_LLM_MODEL = "服务商的准确模型 ID"
$env:WECHAT_AGENT_PRE_SCREEN_MODEL = $env:WECHAT_AGENT_LLM_MODEL
$env:WECHAT_AGENT_SCORING_MODEL = $env:WECHAT_AGENT_LLM_MODEL

# 0 表示分发侧完全不用模型；开启时建议先使用小额日限额。
$env:WECHAT_AGENT_DISTRIBUTION_LLM_DAILY_CALL_LIMIT = "100"
.\start.ps1
```

同一套服务商配置可供内容生产使用，而
`WECHAT_AGENT_DISTRIBUTION_LLM_DAILY_CALL_LIMIT` 只约束采集后的分发处理，
不会关闭内容创作。分发侧达到日限额后自动改用规则回退；遇到服务商返回
401（鉴权失败）或 402（余额/计费失败）会立即熔断，避免后续文章继续请求。
旧版 `DEEPSEEK_API_KEY` 仍兼容，但新配置优先使用服务商无关的
`WECHAT_AGENT_LLM_API_KEY`。

匿名 GitHub 搜索有公开限频。高频运行时可在启动前设置个人 Token：

```powershell
$env:GITHUB_TOKEN = "你的 GitHub Token"
.\start.ps1
```

如果不希望查询公众号公开索引：

```powershell
$env:WECHAT_AGENT_ENABLE_WECHAT_INDEX = "0"
.\start.ps1
```

网络较慢时可延长单个来源超时：

```powershell
$env:WECHAT_AGENT_HTTP_TIMEOUT_SECONDS = "20"
.\start.ps1
```

后台默认每分钟检查一次到期来源；这只是轻量轮询，不代表每个来源每分钟抓取。
如需降低轮询频率，可设置：

```powershell
$env:WECHAT_AGENT_SOURCE_POLL_MINUTES = "5"
.\start.ps1
```

日报默认在上海时间 08:00 固化前一日内容；如需调整小时：

```powershell
$env:WECHAT_AGENT_DAILY_HOUR = "8"
.\start.ps1
```

### 使用持久 SQLite 文件

直接运行 `start.ps1` 时会默认使用 `backend/data/wechat_agent.db`。手动启动
服务时，要让全部项目状态在重启后保留，请先创建数据目录并设置环境变量：

```powershell
New-Item -ItemType Directory -Force .\data | Out-Null
$env:WECHAT_AGENT_DATABASE_URL = "sqlite:///./data/wechat_agent.db"
.\.venv\Scripts\python.exe -m uvicorn wechat_agent.main:app --app-dir src --host 127.0.0.1 --port 8010 --workers 1
```

这个环境变量同时控制文章、任务、日报修订、证据包和草稿版本仓储。使用
`sqlite:///:memory:` 时，各应用实例仍保持隔离，仅适合测试。

## API 调用示例

保持服务运行，在另一个 PowerShell 窗口执行：

```powershell
$task = Invoke-RestMethod -Method Post http://127.0.0.1:8010/api/tasks/live
$task

# 断网演示时才使用：
$task = Invoke-RestMethod -Method Post http://127.0.0.1:8010/api/tasks/demo
$task

$topics = Invoke-RestMethod -Method Get "http://127.0.0.1:8010/api/tasks/$($task.id)/topics"
$topics.items | Select-Object id, title, category

$topicId = $topics.items[0].id
$evidence = Invoke-RestMethod -Method Get "http://127.0.0.1:8010/api/topics/$topicId/evidence"
$evidence

$draft = Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8010/api/topics/$topicId/draft" `
  -ContentType "application/json" `
  -Body "{}"
$draft

$edit = @{
  selected_title = $draft.titles[1]
  body = $draft.body
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Put `
  -Uri "http://127.0.0.1:8010/api/drafts/$($draft.id)" `
  -ContentType "application/json" `
  -Body $edit
```

真实来源和演示样本都可能保留证据不足的情况，所以研究稿可能带有
`insufficient_sources` 阻断项；即使十个主题都生成了草稿，任务也仍是
`review_ready`。这是证据质量门按预期工作，不是接口失败。

## 项目结构

```text
backend/
  src/wechat_agent/
    api/          # FastAPI 路由与输入输出模型
    collectors/   # GitHub、官方 RSS、公众号公开索引与 7/30 天扩窗
    domain/       # 领域模型与端口
    evidence/     # 多来源证据包
    scoring/      # 热度评分
    services/     # 核心流程编排
    storage/      # SQLite 仓储
    topics/       # 去重、分类和 5+5 选题
    writing/      # 规则初稿与质量守门
    web/          # 本地可视化操作页
  tests/
    acceptance/   # 端到端验收
    integration/  # API、流程和仓储测试
    unit/         # 领域算法测试
```
