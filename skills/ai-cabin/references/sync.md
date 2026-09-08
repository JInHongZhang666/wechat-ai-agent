# 数据同步指南

`/api/v1/selected/snapshot` 与 `/api/v1/selected/changes` 已进入
`/openapi-v1.json`。只在建立“当前全部精选”持久镜像时使用；普通资讯查询继续用
items、hot-topics 或 dailies。

## 定时轮询

- 对同一端点至少间隔 60 秒
- 保存 ETag，发送 If-None-Match
- 304 表示内容未变化，复用上次结果

## 首次快照

```text
GET /api/v1/selected/snapshot?fields=minimal&limit=500
```

1. 累积当前页 items，并保存第一页返回的同步水位 `cursor`。
2. `hasMore=true` 时原样回传 `nextPage`，同步水位在整轮分页中保持不变。
3. 全部页面完成后，原子保存完整集合与同步水位。
4. `fields=minimal` 不返回 `summary` 与 `links.original`；后续 changes 会沿用
   cursor 绑定的字段模式。

## 增量变化

```text
GET /api/v1/selected/changes?cursor=<同步水位>&limit=100
```

1. `op=upsert` 按 id 新增或替换，`op=remove` 幂等删除。
2. 整页全部应用成功后才原子保存新 cursor。
3. `hasMore=true` 时继续排空；排空后轮询间隔至少 60 秒。
4. `409 SNAPSHOT_REQUIRED` 时停止旧 cursor，只重建一次完整快照。

响应操作只有两种：

```json
{"op":"upsert","id":"item-id","item":{"id":"item-id"}}
{"op":"remove","id":"item-id"}
```

一次重建仍失败时停止并报告，不循环下载。

## 注意事项

- 同步 `cursor` 与快照翻页 `nextPage` 都不透明，且绝不混用
- 不把 `/api/v1/items?mode=all` 当成"当前全部精选"
- 镜像未完整前不开始 changes
- 不使用重叠时间窗替代 remove 语义
