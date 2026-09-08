# AI 信息小屋人工标注与策略回放

这里保存人工标注工作文件和离线评估报告。系统当前的精选结果只能作为
`suggestion`，不得直接复制为人工真值。

## 当前数据集快照（2026-08-12）

- `gold/news-gold-v1.jsonl`：200 条完整人工标签、0 条合成标签，严格校验通过；
- SHA-256：`c2b8dcf4dca30b9d850a9644f6f95519384dbf9b64ca06402d0e29b5824a6403`；
- `gold/news-gold-v1.1.jsonl`：保留 40 条 Reviewer A 精选复核结果的不可变基线；
- SHA-256：`d151bc253a9927c56fba115c9d2ad357682f5ae6fb25e3a9e2119d49eb583eec`；
- `gold/news-gold-v1.2.jsonl`：40 条精选结论完成双人盲审与冲突裁决后的当前 Gold；
- SHA-256：`e935897204c21af0aba76eb47f3a2580e64b9d49e03d67b207e1c8c7ee495109`；
- `reports/distribution-v3.json`：首份 200 条确定性回放基线；
- `reports/distribution-v3.2-v1.2.json`：当前可解释质量重排策略的 200 条回放报告；
- `reports/news-gold-v1-quality-audit.md`：结构、分布和适用边界审计；
- SQLite v13 保留 v12 的双人盲审与冲突裁决数据，并完成不可达官方 Feed 的审计式换源；
- `STORY-ANNOTATION-GUIDE.md`：R2 Story 文章对标注、冲突裁决和 500 对扩标口径；
- R1 Gold 子门为 `passing`；总门仍为 `blocked`。当前 24/24 来源均达到分层成功率，
  但 Google Gemini CLI、Hugging Face Hub、Mistral Common 三个官方稳定版发布源
  于 2026-08-12 开始新的 7 日观察窗口，当前各 1/1 次成功，不能提前宣告 R1 通过。

本批 200 条的 `story_key` 均为空，因此它是 R1 分发策略的基线，不是 R2 Story
聚类的验收集。v1.2 只对精选结论完成双人复核，分类标签尚未完成同等级复核，
不能据此过拟合分类器；进入 R2 前按同事件正例、相似非同事件难负例扩充到 500 对。

## 1. 导出待标注候选

在 `backend` 目录运行：

```powershell
python -m wechat_agent.evaluation export-candidates `
  --database-url sqlite:///data/wechat_agent.db `
  --output evaluation/workbench/news-labeling-v1.jsonl `
  --limit 200
```

每行包含：

- `schema_version`：当前为 1；
- `evaluation_group`：同一批代码精选的比较分组；
- `article`：当时入库的完整 ArticleRecord；
- `suggestion`：当前策略建议，只帮助标注，不是真值。

## 2. 转换为 gold 文件

人工阅读原始来源后，把每行的 `suggestion` 替换为必填 `label`：

```json
{
  "relevant": true,
  "category": "ai-models",
  "selected": true,
  "primary_source_correct": true,
  "story_key": "openai-model-release-2026-08",
  "creation_worthy": true,
  "evidence_sufficient": true,
  "annotation_method": "human",
  "data_license": "public-source-metadata-only",
  "annotator": "human-name",
  "annotated_at": "2026-08-02T12:00:00+08:00",
  "notes": "官方发布且有明确版本和能力变化。"
}
```

`story_key` 对同一事件使用相同值；不是任何事件簇时可填 `null`。所有标签都
是必填项，部分标注不会通过验证。与 AI 无关的样本必须设置
`relevant=false`、`selected=false`，但仍需给出最接近的五分类用于复核。
`annotation_method` 只能是 `human` 或 `synthetic`；R1 只统计前者。
`data_license` 必须说明该条样本所保留元数据的许可依据，不能为空。

建议文件：`evaluation/gold/news-gold-v1.jsonl`。先完成 200 条，进入 Story
开发前扩充到 500 条。不要提交未经许可的文章全文，只保留标题、摘要、公开
链接和人工判断。

## 3. 校验与回放

```powershell
python -m wechat_agent.evaluation validate `
  --input evaluation/gold/news-gold-v1.jsonl

python -m wechat_agent.evaluation replay `
  --input evaluation/gold/news-gold-v1.jsonl `
  --output evaluation/reports/distribution-v3.json
```

报告包含相关性、精选 precision/recall/F1、分类 accuracy/macro-F1，以及逐条
差异。当前命令执行无密钥的确定性回放；接入真实 LLM 的模型、Prompt、token
和费用回放属于 QUALITY-001 的下一小步。

## 4. 使用内部人工标注工作台

服务连接正式 SQLite 后，打开：

```text
http://127.0.0.1:8010/ops/evaluation
```

工作台会把候选文章、当时的策略建议和分组冻结到 SQLite v12。候选按五分类、
相关性和精选结果分层轮转；点击“补充候选”只补足缺口，不替换已经冻结或已经
标注的样本。策略建议默认折叠，并且不会预填人工判断。

人工操作要求：

- 先打开原始来源，再独立完成所有是/否判断；
- 同一事件使用相同 `story_key`，不属于事件时留空；
- 填写稳定的标注人代号和真实数据许可依据；
- “保存并处理下一条”只会写入完整 `human` 标签；
- 合成标签和部分标签不能通过内部 API 写入正式工作台。

页面可直接下载当前 gold，也可从数据库导出正式文件：

```powershell
python -m wechat_agent.evaluation export-gold `
  --database-url sqlite:///data/wechat_agent.db `
  --output evaluation/gold/news-gold-v1.jsonl
```

已有版本文件默认拒绝覆盖；只有明确需要替换目标时才传 `--force`。正式数据集始终
使用新版本名，不能用 `--force` 覆盖 `news-gold-v1.jsonl`。

## 5. 精选双人盲审与冲突裁决

同一页面切换“精选复核（40 条）”。队列由 `selection-audit-v1` 的稳定抽样规则
一次冻结，重复初始化不会更换样本：

```powershell
Invoke-RestMethod -Method Post `
  http://127.0.0.1:8010/api/ops/evaluation/reviews/selection-audit-v1/seed
```

第一轮 Reviewer A 要求：

- 相关不等于精选；同时判断信息增量、来源可信、时效性和公众号读者价值；
- 默认不选择是/否，先独立判断，再从方向匹配的理由中选一个主要原因；
- 已保存记录不可覆盖，完成项只读；不要让模型或当前策略代替人工答案；
- 已保存记录不可覆盖，并同步写入 v12 的 Reviewer A 审计表；
- 当前 40 条 Reviewer A 已完成，`news-gold-v1.1.jsonl` 及其哈希保持不变。

同一页面切换“第二人盲审”。第二复核人必须是另一位真人；输入真实姓名或长期
使用的稳定代号后只允许绑定一次。身份经 `NFKC + trim + casefold` 比较，不能与
任一初始标注人或 Reviewer A 相同。该模式只显示来源、分类、层级、标题、摘要、
时间和链接，绝不返回或渲染首轮标签、Reviewer A、当前 Gold、分发得分和策略建议。
Reviewer B 的逐条答案不可覆盖，也不会直接更新 Gold。

全部 40 条 Reviewer B 提交前，“冲突裁决”保持锁定，避免后续答案影响盲审。
完成后只展示 A/B 在 `selected` 上真正分歧的条目；裁决人必须与 Reviewer B 不同，
逐条重新选择结论和方向匹配的原因。裁决不可覆盖，只修改当前 Gold 标签里的
`selected` 和 `notes`，原 `annotator`、`annotated_at`、分类及其他标签字段保持不变。

首次让新代码打开正式数据库前，停止服务并在 `backend/data` 内复制一份带时间戳
的备份。迁移会保留 v11 表和 40 条 A 记录，并将 schema 升级为 12。完成 B 盲审和
全部冲突裁决后，使用受门禁命令发布新版本；命令不提供 `--force`，目标存在或
生命周期未达到 `complete` 时退出码为 2：

```powershell
python -m wechat_agent.evaluation export-double-reviewed-gold `
  --database-url sqlite:///./data/wechat_agent.db `
  --review-set-id selection-audit-v1 `
  --output evaluation/gold/news-gold-v1.2.jsonl

python -m wechat_agent.evaluation selection-double-review-report `
  --database-url sqlite:///./data/wechat_agent.db `
  --review-set-id selection-audit-v1 `
  --baseline evaluation/gold/news-gold-v1.1.jsonl `
  --output evaluation/reports/selection-double-review-v1.2.json

python -m wechat_agent.evaluation replay `
  --input evaluation/gold/news-gold-v1.2.jsonl `
  --output evaluation/reports/distribution-v3.2-v1.2.json

python -m wechat_agent.evaluation release-gate `
  --database-url sqlite:///./data/wechat_agent.db `
  --gold evaluation/gold/news-gold-v1.2.jsonl `
  --output evaluation/reports/r1-evidence-current.json `
  --days 7 `
  --required-gold 200 `
  --report-only
```

双审报告包含 A/B 原始一致率、Cohen's kappa（不可定义时给出原因）、2×2 矩阵、
A/B/最终原因分布、逐冲突审计、v1.1/v1.2 哈希及前后策略回放指标。

`distribution-v3.1` 统一了 T1/T1.5/T2 的 60 分门槛，并将每类分发池从 Top 5
调整为 Top 6。`distribution-v3.2` 在此基础上加入可解释的标题主题深度、文化/消费
噪声、远期传闻、官方一手信息和论文实用性调整，并将默认论文配额设为 Top 3；
原始维度分仍由评分层负责，代码只计算透明的精选优先分。v1.2 回放中 selected
precision 从 0.5538 提升到 0.7368，recall 从 0.4675 提升到 0.5455，F1 从
0.5070 提升到 0.6269，false positive 从 29 降到 15。

## 6. Story 事件对准备

R2 不直接从当前 200 条 `story_key` 推断聚类真值。先按
`STORY-ANNOTATION-GUIDE.md` 完成 50 对双人试标，稳定“同一事件/相似非同事件”
边界后扩到 200 对、最终 500 对；算法只生成待标候选，不得自动生成正式标签。

## 7. R1 统一证据闸门

该命令同时审计 7 日信源可靠性、200 条人工 gold 与第一份确定性基线：

```powershell
python -m wechat_agent.evaluation release-gate `
  --database-url sqlite:///data/wechat_agent.db `
  --gold evaluation/gold/news-gold-v1.jsonl `
  --output evaluation/reports/r1-evidence-current.json `
  --days 7 `
  --required-gold 200
```

未达标时命令退出码为 2，适合阻止误发布。采集浸泡或人工标注期间可增加
`--report-only`，仍生成真实的 `blocked` 报告但退出码为 0。报告保留逐源窗口、
成功率、未达标来源、人工/合成样本数、数据集 SHA-256 和 baseline；合成示例
永远不会计入人工 gold。
