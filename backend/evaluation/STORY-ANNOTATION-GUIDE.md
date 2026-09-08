# Story 文章对人工标注指南 v1

本指南用于构建 R2 Story 事件层的文章对（pair）验收集。它回答一个窄问题：
**两篇文章是否在报道同一个可指认事件？** 它不判断文章是否优质、是否相关、
是否应该精选，也不把“主题相似”当作“同一事件”。

## 1. 标注前提与操作顺序

1. 分别打开两篇文章的原始来源，先读标题、摘要、发布时间和发布主体；信息不足时读正文。
2. 独立提取双方的主体、产品/版本、动作、时间、地点等事件锚点。
3. 在不看系统聚类建议的情况下判断 `same_story`。
4. 选择最主要的 `relation_reason`，补充锚点、主来源和必要说明。
5. 只有确实无法从公开证据裁决时才使用 `uncertain`；不能用它代替认真查证。

文章对标注采用无序语义：交换 A/B 后，`same_story` 不应改变。每条记录必须保留
稳定的标注人、时间和数据许可，不保存未经许可的文章全文。

## 2. 完整记录格式

```json
{
  "article_a_id": "feed-openai-gpt-56-release",
  "article_b_id": "publisher-gpt-56-launch-analysis",
  "same_story": "yes",
  "relation_reason": "same_event_update",
  "entity_anchors": {
    "organizations": ["OpenAI"],
    "products": ["GPT-5.6"],
    "versions": ["5.6"],
    "people": []
  },
  "time_anchor": "2026-08-03",
  "location_anchor": null,
  "hard_negative": false,
  "primary_source_article_id": "feed-openai-gpt-56-release",
  "annotation_method": "human",
  "data_license": "public-source-metadata-only",
  "annotator": "reviewer-a",
  "annotated_at": "2026-08-03T00:00:00Z",
  "notes": "官方发布与同日媒体解析指向同一版本发布事件。"
}
```

字段规则：

- `same_story`：只能是 `yes`、`no`、`uncertain`。
- `relation_reason`：使用第 4、5 节的稳定原因码；每对只填一个主要原因。
- `entity_anchors`：只填公开证据能确认的规范化实体；别名应归一到同一名称。
- `time_anchor`：事件发生或首次公开日期，未知时为 `null`，不是文章抓取日期。
- `location_anchor`：地点对事件有区分作用时填写，否则为 `null`。
- `hard_negative`：仅在 `same_story=no` 且两篇文章表面高度相似时为 `true`。
- `primary_source_article_id`：`yes` 时优先指向事件一手来源；无法判断可为 `null`；
  `no` 或 `uncertain` 时固定为 `null`。
- `annotation_method`：正式验收集固定为 `human`。
- `data_license`：说明保存的公开元数据许可依据，不得为空。
- `notes`：记录边界、冲突或裁决依据，不复制文章长段落。

## 3. “同一 Story”的必要条件

只有当两篇文章共享同一个核心事件断言时标 `yes`。至少应同时对齐：

- 主体：实施动作的组织、人物或项目一致；
- 客体：同一产品、版本、论文、交易、政策或事故；
- 动作：发布、融资、开源、下架、收购、监管裁决等核心动作一致；
- 时间：属于同一次事件或其短周期事实更新，而非另一轮相似动作。

地点、数值和状态不一定完全相同：后续报道可能补充新数字或修正早期信息。只要它们
仍在更新同一核心事件，可以是同一 Story；如果核心动作或版本已变化，则不是。

## 4. 正例类型（`same_story=yes`）

至少覆盖以下四类：

1. `same_event_republication`：同一公告被不同媒体转载、翻译或改写，核心事实不变。
2. `official_and_coverage`：官方一手发布与媒体对该次发布的报道指向同一事件。
3. `same_event_update`：同一事件的后续确认、数字补充、影响说明或状态推进，没有形成新的核心动作。
4. `same_event_analysis`：新闻报道与针对该具体事件的分析/解读；分析必须明确绑定该次事件，而非泛主题文章。

可接受例子：官方发布某模型、媒体同日报道该模型发布；融资传闻后由公司确认且交易
主体和轮次一致；同一服务故障的发生报道与数小时后的恢复更新。

## 5. 负例与难负例（`same_story=no`）

普通无关文章使用 `unrelated_events`。下列至少六类是 R2 必须重点覆盖的难负例，
标记 `hard_negative=true`：

1. `same_org_different_event`：同一公司在相近时间发生不同动作，例如发布模型与调整价格。
2. `same_product_different_version`：同一产品的不同版本、不同批次发布或独立功能更新。
3. `same_topic_different_actor`：主题和关键词相同，但实施主体不同，例如两家公司分别发布 Agent。
4. `prediction_vs_occurrence`：发布预测、传闻或预告，与后来实际发生的事件不是同一已确认断言。
5. `overview_vs_specific_event`：行业盘点、教程或趋势文章提到某事件，但文章核心不是该事件。
6. `similar_title_different_time_or_place`：标题近似，但事件日期、国家/地区或会议场次不同。
7. `followup_new_event`：前一事件引发了新的监管、诉讼、下架、收购或产品动作；存在因果关系但核心动作已改变。
8. `same_research_area_different_work`：同一研究方向、相似方法名或同一团队的不同论文/实验。

因果相关、人物相同、共享背景或互相引用都不足以判定同一 Story。一个日报/合集同时
提到多个事件时，只在其核心叙述明显围绕目标事件时才可与单篇报道合并。

## 6. `uncertain` 的使用条件

仅在以下情况使用 `same_story=uncertain`，并设置 `relation_reason=insufficient_evidence`：

- 原始来源不可访问，标题和摘要不足以区分版本、时间或动作；
- 两个可靠来源对关键主体、版本、日期存在冲突，公开证据无法排除任一方；
- 一个来源只有传闻，另一个来源描述含糊，无法确认是否指向同一具体事件；
- 事件持续时间长且边界不清，无法判断是同一状态更新还是新的独立事件。

`uncertain` 时 `hard_negative=false`、`primary_source_article_id=null`，`notes` 必须写明
缺失或冲突的证据。能通过打开原文、核对日期或官方公告解决的，不得标 uncertain。

## 7. 主来源判定

同一 Story 内优先级依次为：

1. 事件实施方的官方公告、代码仓库、论文、监管文件或公开记录；
2. 直接采访、现场记录或拥有原始数据的报道；
3. 明确引用一手材料且事实完整的专业媒体；
4. 聚合、转载、评论和二次解读。

“最早抓取”不等于主来源，“篇幅最长”也不等于主来源。若两篇都不是一手来源或无法
可靠判断，允许 `primary_source_article_id=null`，不要猜测。

## 8. 双人标注与冲突裁决

- 每对先由两名标注人独立提交；双方在提交前不得查看对方答案或系统聚类结果。
- `same_story` 一致但原因码不同：核对核心事件边界，由双方统一主要原因码并保留原始记录。
- `yes/no` 冲突：第三名裁决人重新阅读原文，显式比较主体、客体、动作、时间和地点。
- 任一方为 `uncertain`：先补查一手来源；仍无法判断时交第三人裁决，不以多数自动覆盖。
- 裁决结果另存新记录，必须保留两份独立原始标签、裁决人、时间和说明，禁止原地改写。
- 若同类冲突率超过 15%，暂停扩标，先修订边界示例并对已标样本回查。

一致性报告至少包含：`yes/no/uncertain` 分布、原始一致率、Cohen's kappa、难负例一致率、
冲突原因分布和每个分类的样本数。

## 9. 500 对扩标分层

首版 500 对验收集按事件对而非单篇文章计数，目标分层如下：

| 分层 | 目标数 | 占比 | 说明 |
|---|---:|---:|---|
| 同事件正例 | 200 | 40% | 四类正例均不少于 35 对，包含短周期更新 |
| 相似非同事件难负例 | 200 | 40% | 六类核心难负例均不少于 25 对 |
| 普通负例 | 75 | 15% | 跨分类、跨主体，用于校验基础区分能力 |
| 不确定边界池 | 25 | 5% | 经补查仍不确定，单独报告，不混入确定性主指标 |

同时满足以下覆盖约束：

- 五个内容分类各不少于 70 对，剩余 150 对按真实流量分配；
- 至少 40% 的确定性样本来自不同 `publisherGroup`，防止只学习站内转载格式；
- 正例覆盖同日、1～3 日更新和 4～14 日长尾更新，时间较长的对必须说明为何仍属同一事件；
- 至少 30% 难负例共享一个强实体锚点，至少 20% 共享相似标题关键词；
- 同一组织或同一产品不得超过确定性样本的 10%，避免头部实体支配结果；
- 训练、调参与最终验收按 Story 分组切分，同一 Story 的文章不得跨集合泄漏。

先做 50 对试标并计算一致性；边界稳定后扩到 200 对，再完成 500 对。任何阶段都不能
由当前聚类算法自动生成真值；算法只能提供待标候选，人工结论必须独立产生并可审计。

## 10. R2 启动门

只有同时满足以下条件，才开始以这批数据调试聚类或 Story 生命周期：

- 500 对均通过格式和许可校验；
- 确定性样本双人原始一致率不低于 85%，难负例一致率不低于 80%；
- 争议样本已有独立裁决记录，`uncertain` 不计入确定性 precision/recall/F1；
- 数据按 Story 分组切分，未发生训练/验收泄漏；
- 评测报告能分别给出同事件召回、难负例精度和明显重复展示率。

达到以上门槛后，才依次实现候选生成、确定性去重、语义相似、Story 模型与合并/拆分
审计。未达到门槛时继续完善数据，不用调阈值掩盖标注边界问题。

## 11. 50 对正式双盲批次操作

正式批次目录为 `evaluation/story-annotation/batches/story-pairs-shadow-v1-50/`，候选文件
SHA-256 固定为 `8b1a7e4565f6176f49c0e9fd85790ff2acdad2e3ae819392696666760dec2070`。
不得把 shadow 数据库、抽样分层审计、算法分数、建议或 veto 原因交给标注人。

分别向 Reviewer A 和 Reviewer B 交付同一份 `manifest.json`，以及各自的模板。两份模板
初始字节完全一致；双方分别填写 `annotator`、`annotatedAt` 和判断字段，不共享工作文件。
正式 JSONL 使用 camelCase 字段名。

在 backend 目录执行单人校验，每条命令只接触一份提交：

```powershell
./.venv/Scripts/python.exe -m wechat_agent.story_annotation validate-submission `
  --manifest evaluation/story-annotation/batches/story-pairs-shadow-v1-50/manifest.json `
  --submission evaluation/story-annotation/submissions/reviewer-a.jsonl `
  --reviewer reviewer-a

./.venv/Scripts/python.exe -m wechat_agent.story_annotation validate-submission `
  --manifest evaluation/story-annotation/batches/story-pairs-shadow-v1-50/manifest.json `
  --submission evaluation/story-annotation/submissions/reviewer-b.jsonl `
  --reviewer reviewer-b
```

只有 Reviewer B 完成全部 50 对后才会读取 Reviewer A 并解锁冲突：

```powershell
./.venv/Scripts/python.exe -m wechat_agent.story_annotation agreement `
  --manifest evaluation/story-annotation/batches/story-pairs-shadow-v1-50/manifest.json `
  --reviewer-a evaluation/story-annotation/submissions/reviewer-a.jsonl `
  --reviewer-b evaluation/story-annotation/submissions/reviewer-b.jsonl `
  --output evaluation/story-annotation/evidence/agreement-50.json
```

记录上一命令返回的 `sha256`，第三人完成冲突裁决后执行：

```powershell
./.venv/Scripts/python.exe -m wechat_agent.story_annotation validate-adjudication `
  --manifest evaluation/story-annotation/batches/story-pairs-shadow-v1-50/manifest.json `
  --agreement evaluation/story-annotation/evidence/agreement-50.json `
  --reviewer-a evaluation/story-annotation/submissions/reviewer-a.jsonl `
  --reviewer-b evaluation/story-annotation/submissions/reviewer-b.jsonl `
  --adjudication evaluation/story-annotation/submissions/adjudication-50.jsonl `
  --agreement-sha256 <agreement 命令返回的 sha256> `
  --output evaluation/story-annotation/evidence/adjudication-50.json
```

最后运行 50 对试标门禁。该门禁只判断是否可以进入 200 对扩标，不会开启公开 Story：

```powershell
./.venv/Scripts/python.exe -m wechat_agent.story_annotation release-gate `
  --stage pilot-50 `
  --manifest evaluation/story-annotation/batches/story-pairs-shadow-v1-50/manifest.json `
  --agreement evaluation/story-annotation/evidence/agreement-50.json `
  --adjudication evaluation/story-annotation/evidence/adjudication-50.json `
  --output evaluation/story-annotation/evidence/gate-50.json
```

所有证据文件默认禁止覆盖。需要重跑时使用新的文件名，保留原文件及其 SHA-256。
