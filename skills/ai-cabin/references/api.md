# AI 信息小屋 v1 API 参考

Base URL：`http://127.0.0.1:8010`
匿名只读，不需要 API Key。

公开机器规范：`GET /openapi-v1.json`。该规范只包含稳定分发端点，不包含本地
信源运维或公众号创作任务。

## 最近资讯

`GET /api/v1/items`

| 参数 | 说明 |
|------|------|
| `mode` | `selected` 或 `all`；默认 `selected` |
| `window` | `24h` 或 `7d`；默认 `7d` |
| `by` | `timeline` 或 `published`；默认 `timeline` |
| `category` | `ai-models`、`ai-products`、`industry`、`paper`、`tip` |
| `q` | 2-200 字关键词搜索 |
| `limit` | 1-100；默认 50 |
| `cursor` | 原样回传上一页的 `page.nextCursor` |

响应：`{schemaVersion, query{..., ordering}, items[], page{count, hasMore, nextCursor, cursorVersion}}`

## 当前热点

`GET /api/v1/hot-topics`

响应：`{schemaVersion, count, items[]}` — 按多源讨论强度排列。

只有至少两个独立 `publisherGroup` 佐证的标题组才返回。当前阶段没有正式
Story 时 `links.story` 可缺失，不得据此猜造 Story ID。

## 日报与指纹

```
GET /api/v1/dailies?limit=7
GET /api/v1/dailies/latest
GET /api/v1/dailies/{YYYY-MM-DD}
GET /api/v1/fingerprint
```

latest 返回 404 时只查询一次日报索引并使用其中最近的真实日期，不猜“昨天”。

## 本地创作扩展（不属于公开分发 OpenAPI）

```
POST /api/v1/tasks/demo     → 创建演示任务
POST /api/v1/tasks/live     → 创建实时采集任务
GET  /api/v1/tasks/{id}     → 任务状态
GET  /api/v1/tasks/{id}/topics → 选题列表
```

### 证据与草稿

```
GET  /api/v1/topics/{id}/evidence → 证据包
GET  /api/v1/topics/{id}/draft    → 最新草稿
POST /api/topics/{id}/draft       → 生成草稿 (legacy)
PUT  /api/drafts/{id}             → 编辑草稿 (legacy)
GET  /api/drafts/{id}/export      → 导出草稿 (legacy)
```

## 分页

1. 处理当前页全部 items
2. `page.hasMore=true` 时，回传 `page.nextCursor` 请求下一页
3. `page.hasMore=false` 时结束

## ETag 条件请求

对同一完整 URL 保存响应 `ETag`；下次发送 `If-None-Match`。`304` 表示内容未变化。
直接复用上一次响应体，不要为获取正文再发一次无条件请求。
