# 错误与重试

## v1 应用错误

标准错误使用 Problem JSON：

```json
{
  "type": "/problems/invalid-request",
  "title": "Invalid request",
  "status": 422,
  "detail": "Human-readable explanation",
  "code": "INVALID_REQUEST",
  "requestId": "req_xxx"
}
```

按稳定 `code` 分支：

| code | HTTP | 处理 |
|------|------|------|
| `NOT_FOUND` | 404 | 不重试 |
| `INVALID_REQUEST` | 400/422 | 修正参数，不自动改变用户问题 |
| `INVALID_CURSOR` | 400 | 从第一页重新发起查询 |
| `RATE_LIMITED` | 429 | 遵守 Retry-After，等 60s |
| `TEMPORARILY_UNAVAILABLE` | 503 | 退避后告知用户暂不可用 |
| `INTERNAL_FAILURE` | 500 | 最多重试 2 次，指数退避 |

## 重试

- `400/404`：不盲目重试
- `409 SNAPSHOT_REQUIRED`：停止旧 cursor，按同步文档重建一次快照
- `429`：按 Retry-After 等待
- `5xx` 或超时：最多重试 2 次
- 仍失败：说明暂不可用，不得用训练记忆冒充实时数据
- 日报 404：只查询一次有界日报索引，不自行拼日期
