"""MongoDB persistence via Motor (async driver) for all domain records.

Replaces the previous SQLite storage layer. Uses four collections:
  - articles       → ArticleRecord documents
  - tasks          → Task lifecycle records
  - evidence_packs → EvidencePack documents
  - drafts         → DraftRecord documents with full version history

All public methods are synchronous (using asyncio.run internally) for
compatibility with FastAPI's sync endpoint handlers.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, ReplaceOne, ReturnDocument
from pymongo.errors import DuplicateKeyError

from wechat_agent.domain.models import (
    AccountProfile,
    ArticleRecord,
    CurationDecision,
    CreatorProject,
    ContentOpportunity,
    CreationReplayReport,
    DraftDocument,
    EngagementMetrics,
    EvidencePack,
    HeatConfidence,
    QualityIssue,
    LlmCallRecord,
    PromptVersion,
    SourceType,
    TopicCategory,
)
from wechat_agent.domain.fingerprints import article_content_fingerprint
from wechat_agent.domain.sync import (
    SelectedChange,
    SelectedChangesPage,
    SelectedFieldMode,
    SelectedSnapshotPage,
    SelectedSyncItem,
    SnapshotRequiredError,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _as_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _run_async(coro):
    """Run an async coroutine synchronously."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Already in an event loop — use thread pool
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor() as executor:
        future = executor.submit(asyncio.run, coro)
        return future.result()


# ---------------------------------------------------------------------------
# MongoDB connection helper
# ---------------------------------------------------------------------------

def create_mongo_client(mongo_uri: str) -> AsyncIOMotorClient:
    """Create a Motor async client connected to the given MongoDB URI."""
    return AsyncIOMotorClient(mongo_uri)


# ---------------------------------------------------------------------------
# Article repository
# ---------------------------------------------------------------------------

class MongoArticleRepository:
    """Store ``ArticleRecord`` values in MongoDB, keyed by source_url."""

    def __init__(self, mongo_uri: str) -> None:
        self._client = create_mongo_client(mongo_uri)
        db_name = mongo_uri.rsplit("/", 1)[-1].split("?")[0] or "ai_cabin"
        self.db: AsyncIOMotorDatabase = self._client[db_name]
        self._collection = self.db.articles

    def save_many(self, articles: list[ArticleRecord]) -> int:
        """Sync — upsert articles by source_url."""
        if not articles:
            return 0
        return _run_async(self._save_many_async(articles))

    def filter_changed(
        self,
        articles: Sequence[ArticleRecord],
    ) -> list[ArticleRecord]:
        """Return only new URLs or rows whose content fingerprint changed."""
        if not articles:
            return []
        return _run_async(self._filter_changed_async(articles))

    async def _filter_changed_async(
        self,
        articles: Sequence[ArticleRecord],
    ) -> list[ArticleRecord]:
        unique = {article.source_url: article for article in articles}
        cursor = self._collection.find(
            {"source_url": {"$in": list(unique)}},
            {"source_url": 1, "content_fingerprint": 1},
        )
        rows = await cursor.to_list(length=len(unique))
        stored = {
            row["source_url"]: row.get("content_fingerprint")
            for row in rows
        }
        return [
            article
            for url, article in unique.items()
            if stored.get(url) != article_content_fingerprint(article)
        ]

    async def _save_many_async(self, articles: list[ArticleRecord]) -> int:
        requests = [
            ReplaceOne(
                {"source_url": doc["source_url"]},
                doc,
                upsert=True,
            )
            for doc in (_article_to_doc(a) for a in articles)
        ]
        await self._collection.bulk_write(requests)
        return len(articles)

    def list_recent(self, since: datetime, limit: int = 100) -> list[ArticleRecord]:
        """Sync — list articles published since given datetime."""
        return _run_async(self._list_recent_async(since, limit))

    async def _list_recent_async(self, since: datetime, limit: int = 100) -> list[ArticleRecord]:
        since_utc = _as_utc(since).isoformat()
        cursor = (
            self._collection.find({"published_at": {"$gte": since_utc}})
            .sort("published_at", -1)
            .limit(limit)
        )
        rows = await cursor.to_list(length=limit)
        return [_article_from_doc(row) for row in rows]

    def list_all(self, limit: int = 10000) -> list[ArticleRecord]:
        """Sync — list the durable article collection without a time window."""
        return _run_async(self._list_all_async(limit))

    async def _list_all_async(self, limit: int = 10000) -> list[ArticleRecord]:
        cursor = self._collection.find({}).sort(
            [("published_at", -1), ("id", -1)]
        ).limit(limit)
        rows = await cursor.to_list(length=limit)
        return [_article_from_doc(row) for row in rows]

    async def close(self) -> None:
        self._client.close()


def _article_to_doc(article: ArticleRecord) -> dict[str, Any]:
    return {
        "source_url": article.source_url,
        "id": article.id,
        "source_id": article.source_id,
        "title": article.title,
        "published_at": _as_utc(article.published_at).isoformat(),
        "collected_at": _as_utc(article.collected_at).isoformat(),
        "summary": article.summary,
        "category": article.category.value,
        "engagement": (
            article.engagement.model_dump() if article.engagement is not None else None
        ),
        "source_metadata": {
            "source_name": article.source_name,
            "source_url": article.source_url,
            "source_type": article.source_type.value,
            "source_identity": article.source_identity,
            "source_type_verified": article.source_type_verified,
            "source_verification_basis": article.source_verification_basis,
            "source_confidence": article.source_confidence,
            "tier": article.tier,
        },
        "content_fingerprint": article_content_fingerprint(article),
    }


def _article_from_doc(doc: dict[str, Any]) -> ArticleRecord:
    source_metadata = doc.get("source_metadata", {})
    engagement = doc.get("engagement")
    return ArticleRecord(
        id=doc["id"],
        source_id=doc.get("source_id"),
        title=doc["title"],
        source_name=source_metadata.get("source_name", ""),
        source_url=doc["source_url"],
        source_type=SourceType(source_metadata.get("source_type", "secondary")),
        source_identity=source_metadata.get("source_identity"),
        source_type_verified=source_metadata.get("source_type_verified", False),
        source_verification_basis=source_metadata.get("source_verification_basis"),
        source_confidence=source_metadata.get("source_confidence"),
        tier=source_metadata.get("tier"),
        published_at=datetime.fromisoformat(doc["published_at"]),
        collected_at=datetime.fromisoformat(doc["collected_at"]),
        summary=doc["summary"],
        category=TopicCategory(doc["category"]),
        engagement=(
            EngagementMetrics.model_validate(engagement)
            if engagement is not None
            else None
        ),
    )


# ---------------------------------------------------------------------------
# Curation decision repository
# ---------------------------------------------------------------------------


class MongoCurationDecisionRepository:
    """Persist one precomputed distribution decision per article."""

    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self._collection = db.curation_decisions

    def save_many(self, decisions: list[CurationDecision]) -> int:
        if not decisions:
            return 0
        return _run_async(self._save_many_async(decisions))

    async def _save_many_async(self, decisions: list[CurationDecision]) -> int:
        requests = [
            ReplaceOne(
                {"_id": decision.article_id},
                {
                    "_id": decision.article_id,
                    "distribution_score": decision.distribution_score,
                    "selected": decision.selected,
                    "eligible": decision.eligible,
                    "components": decision.components,
                    "threshold": decision.threshold,
                    "reason": decision.reason,
                    "score_confidence": decision.score_confidence.value,
                    "strategy_version": decision.strategy_version,
                    "scored_at": _as_utc(decision.scored_at).isoformat(),
                    "updated_at": _iso_now(),
                },
                upsert=True,
            )
            for decision in decisions
        ]
        await self._collection.bulk_write(requests)
        return len(decisions)

    def get_many(self, article_ids: list[str]) -> dict[str, CurationDecision]:
        if not article_ids:
            return {}
        return _run_async(self._get_many_async(article_ids))

    async def _get_many_async(
        self,
        article_ids: list[str],
    ) -> dict[str, CurationDecision]:
        cursor = self._collection.find({"_id": {"$in": article_ids}})
        rows = await cursor.to_list(length=len(article_ids))
        return {
            row["_id"]: CurationDecision(
                article_id=row["_id"],
                distribution_score=row["distribution_score"],
                selected=row["selected"],
                eligible=row.get("eligible", True),
                components=row.get("components", {}),
                threshold=row["threshold"],
                reason=row["reason"],
                score_confidence=HeatConfidence(row["score_confidence"]),
                strategy_version=row["strategy_version"],
                scored_at=datetime.fromisoformat(row["scored_at"]),
            )
            for row in rows
        }


# ---------------------------------------------------------------------------
# Selected snapshot/changes repository
# ---------------------------------------------------------------------------


class MongoSelectedSyncRepository:
    """MongoDB adapter for the durable AIHOT selected synchronization state."""

    def __init__(
        self,
        db: AsyncIOMotorDatabase,
        *,
        max_changes: int = 10000,
        snapshot_ttl_seconds: int = 900,
    ) -> None:
        self._items = db.selected_sync_items
        self._changes = db.selected_sync_changes
        self._state = db.selected_sync_state
        self._cursors = db.selected_sync_cursors
        self._snapshots = db.selected_sync_snapshots
        self._pages = db.selected_sync_snapshot_pages
        self._max_changes = max(1, max_changes)
        self._snapshot_ttl = timedelta(
            seconds=max(60, snapshot_ttl_seconds)
        )

    def reconcile(self, items: list[SelectedSyncItem]) -> int:
        return _run_async(self._reconcile_async(items))

    async def _reconcile_async(self, items: list[SelectedSyncItem]) -> int:
        await self._ensure_indexes_async()
        incoming = {item.id: item for item in items}
        current_rows = await self._items.find({}).to_list(length=100000)
        current = {row["_id"]: row for row in current_rows}
        changed = 0
        now = _iso_now()
        for item_id in sorted(incoming):
            item = incoming[item_id]
            content_hash = _selected_sync_hash(item)
            existing = current.get(item_id)
            if existing is not None and existing.get("content_hash") == content_hash:
                continue
            sequence = await self._next_sequence_async()
            await self._changes.insert_one(
                {
                    "_id": sequence,
                    "op": "upsert",
                    "item_id": item.id,
                    "payload_default": item.default,
                    "payload_minimal": item.minimal,
                    "changed_at": now,
                }
            )
            # Append first: a crash may duplicate an idempotent upsert on the
            # next reconcile, but it can never make a mutation disappear.
            await self._items.replace_one(
                {"_id": item.id},
                {
                    "_id": item.id,
                    "payload_default": item.default,
                    "payload_minimal": item.minimal,
                    "content_hash": content_hash,
                    "updated_at": now,
                },
                upsert=True,
            )
            changed += 1
        for item_id in sorted(set(current) - set(incoming)):
            sequence = await self._next_sequence_async()
            await self._changes.insert_one(
                {
                    "_id": sequence,
                    "op": "remove",
                    "item_id": item_id,
                    "payload_default": None,
                    "payload_minimal": None,
                    "changed_at": now,
                }
            )
            await self._items.delete_one({"_id": item_id})
            changed += 1
        if changed:
            await self._prune_changes_async()
        return changed

    def snapshot(
        self,
        *,
        fields: SelectedFieldMode,
        limit: int,
        page: str | None = None,
    ) -> SelectedSnapshotPage:
        return _run_async(
            self._snapshot_async(fields=fields, limit=limit, page=page)
        )

    async def _snapshot_async(
        self,
        *,
        fields: SelectedFieldMode,
        limit: int,
        page: str | None,
    ) -> SelectedSnapshotPage:
        await self._ensure_indexes_async()
        await self._cleanup_expired_snapshots_async()
        if page is not None:
            page_row = await self._pages.find_one({"_id": page})
            if page_row is None:
                raise SnapshotRequiredError
            snapshot_row = await self._snapshots.find_one(
                {"_id": page_row["snapshot_id"]}
            )
            if (
                snapshot_row is None
                or snapshot_row["fields"] != fields
                or int(snapshot_row["page_limit"]) != limit
            ):
                raise SnapshotRequiredError
            return await self._snapshot_page_async(
                snapshot_row,
                int(page_row["page_index"]),
            )

        state = await self._state_async()
        cursor = await self._cursor_for_async(
            int(state["latest_sequence"]), fields
        )
        active_snapshot = await self._snapshots.find_one(
            {
                "cursor_token": cursor,
                "fields": fields,
                "page_limit": limit,
            },
            sort=[("created_at", -1)],
        )
        if active_snapshot is not None:
            return await self._snapshot_page_async(active_snapshot, 0)
        rows = await self._items.find({}).to_list(length=100000)
        payload_key = (
            "payload_minimal" if fields == "minimal" else "payload_default"
        )
        payloads = [row[payload_key] for row in rows]
        payloads.sort(
            key=lambda item: (
                str(item.get("timelineAt") or ""),
                str(item.get("id") or ""),
            ),
            reverse=True,
        )
        now = datetime.now(UTC)
        snapshot_id = f"ss_{uuid4().hex}"
        snapshot = {
            "_id": snapshot_id,
            "cursor_token": cursor,
            "fields": fields,
            "page_limit": limit,
            "items": payloads,
            "created_at": now.isoformat(),
            "expires_at": (now + self._snapshot_ttl).isoformat(),
        }
        await self._snapshots.insert_one(snapshot)
        page_count = max(1, (len(payloads) + limit - 1) // limit)
        if page_count > 1:
            await self._pages.insert_many(
                [
                    {
                        "_id": f"sp_{uuid4().hex}",
                        "snapshot_id": snapshot_id,
                        "page_index": page_index,
                    }
                    for page_index in range(1, page_count)
                ]
            )
        return await self._snapshot_page_async(snapshot, 0)

    def changes(self, *, cursor: str, limit: int) -> SelectedChangesPage:
        return _run_async(self._changes_async(cursor=cursor, limit=limit))

    async def _changes_async(
        self,
        *,
        cursor: str,
        limit: int,
    ) -> SelectedChangesPage:
        await self._ensure_indexes_async()
        state = await self._state_async()
        cursor_row = await self._cursors.find_one({"_id": cursor})
        if (
            cursor_row is None
            or int(cursor_row["sequence"]) < int(state["floor_sequence"])
        ):
            raise SnapshotRequiredError
        rows = await (
            self._changes.find({"_id": {"$gt": int(cursor_row["sequence"])}})
            .sort("_id", ASCENDING)
            .limit(limit + 1)
            .to_list(length=limit + 1)
        )
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        fields = str(cursor_row["fields"])
        next_cursor = cursor
        if page_rows:
            next_cursor = await self._cursor_for_async(
                int(page_rows[-1]["_id"]), fields
            )
        changes = [
            SelectedChange(
                op=row["op"],
                id=row["item_id"],
                item=(
                    row["payload_minimal"]
                    if fields == "minimal"
                    else row["payload_default"]
                )
                if row["op"] == "upsert"
                else None,
            )
            for row in page_rows
        ]
        return SelectedChangesPage(
            fields=fields,  # type: ignore[arg-type]
            changes=changes,
            cursor=next_cursor,
            has_more=has_more,
        )

    async def _ensure_indexes_async(self) -> None:
        await self._cursors.create_index(
            [("sequence", ASCENDING), ("fields", ASCENDING)],
            unique=True,
        )
        await self._pages.create_index(
            [("snapshot_id", ASCENDING), ("page_index", ASCENDING)],
            unique=True,
        )

    async def _state_async(self) -> dict[str, Any]:
        await self._state.update_one(
            {"_id": "state"},
            {
                "$setOnInsert": {
                    "latest_sequence": 0,
                    "floor_sequence": 0,
                    "updated_at": _iso_now(),
                }
            },
            upsert=True,
        )
        state = await self._state.find_one({"_id": "state"})
        assert state is not None
        return state

    async def _next_sequence_async(self) -> int:
        state = await self._state.find_one_and_update(
            {"_id": "state"},
            {
                "$inc": {"latest_sequence": 1},
                "$set": {"updated_at": _iso_now()},
                "$setOnInsert": {"floor_sequence": 0},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return int(state["latest_sequence"])

    async def _cursor_for_async(
        self,
        sequence: int,
        fields: SelectedFieldMode | str,
    ) -> str:
        existing = await self._cursors.find_one(
            {"sequence": sequence, "fields": fields}
        )
        if existing is not None:
            return str(existing["_id"])
        token = f"sc_{uuid4().hex}"
        try:
            await self._cursors.insert_one(
                {
                    "_id": token,
                    "sequence": sequence,
                    "fields": fields,
                    "created_at": _iso_now(),
                }
            )
            return token
        except DuplicateKeyError:
            existing = await self._cursors.find_one(
                {"sequence": sequence, "fields": fields}
            )
            if existing is None:
                raise
            return str(existing["_id"])

    async def _snapshot_page_async(
        self,
        snapshot: dict[str, Any],
        page_index: int,
    ) -> SelectedSnapshotPage:
        limit = int(snapshot["page_limit"])
        items = list(snapshot.get("items") or [])
        start = page_index * limit
        has_more = start + limit < len(items)
        next_page = None
        if has_more:
            page_row = await self._pages.find_one(
                {
                    "snapshot_id": snapshot["_id"],
                    "page_index": page_index + 1,
                }
            )
            next_page = str(page_row["_id"]) if page_row else None
        return SelectedSnapshotPage(
            fields=snapshot["fields"],
            items=items[start : start + limit],
            cursor=snapshot["cursor_token"],
            has_more=has_more,
            next_page=next_page,
        )

    async def _cleanup_expired_snapshots_async(self) -> None:
        rows = await self._snapshots.find(
            {"expires_at": {"$lt": _iso_now()}}, {"_id": 1}
        ).to_list(length=10000)
        snapshot_ids = [row["_id"] for row in rows]
        if not snapshot_ids:
            return
        await self._pages.delete_many({"snapshot_id": {"$in": snapshot_ids}})
        await self._snapshots.delete_many({"_id": {"$in": snapshot_ids}})

    async def _prune_changes_async(self) -> None:
        count = await self._changes.count_documents({})
        if count <= self._max_changes:
            return
        retained = await (
            self._changes.find({}, {"_id": 1})
            .sort("_id", ASCENDING)
            .skip(count - self._max_changes)
            .limit(1)
            .to_list(length=1)
        )
        if not retained:
            return
        floor_sequence = int(retained[0]["_id"]) - 1
        await self._changes.delete_many({"_id": {"$lte": floor_sequence}})
        await self._cursors.delete_many({"sequence": {"$lt": floor_sequence}})
        await self._state.update_one(
            {"_id": "state"},
            {
                "$set": {
                    "floor_sequence": floor_sequence,
                    "updated_at": _iso_now(),
                }
            },
        )


def _selected_sync_hash(item: SelectedSyncItem) -> str:
    body = json.dumps(
        {"default": item.default, "minimal": item.minimal},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(body.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Task repository
# ---------------------------------------------------------------------------

class MongoTaskRepository:
    """Persist task lifecycle records in MongoDB."""

    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self._collection = db.tasks

    def save(
        self, task_id: str, status: str, status_history: list[str],
        mode: str, collected_article_count: int, window_days: int,
        expanded_categories: list[str], collection_warnings: list[str],
        source_counts: dict[str, int], topic_ids: list[str],
        drafted_topic_ids: list[str], result_json: str | None,
    ) -> None:
        _run_async(self._save_async(
            task_id, status, status_history, mode,
            collected_article_count, window_days,
            expanded_categories, collection_warnings,
            source_counts, topic_ids, drafted_topic_ids, result_json,
        ))

    async def _save_async(
        self, task_id: str, status: str, status_history: list[str],
        mode: str, collected_article_count: int, window_days: int,
        expanded_categories: list[str], collection_warnings: list[str],
        source_counts: dict[str, int], topic_ids: list[str],
        drafted_topic_ids: list[str], result_json: str | None,
    ) -> None:
        now = _iso_now()
        doc = {
            "_id": task_id, "status": status,
            "status_history": status_history, "mode": mode,
            "collected_article_count": collected_article_count,
            "window_days": window_days,
            "expanded_categories": expanded_categories,
            "collection_warnings": collection_warnings,
            "source_counts": source_counts, "topic_ids": topic_ids,
            "drafted_topic_ids": drafted_topic_ids,
            "result_json": result_json,
            "created_at": now, "updated_at": now,
        }
        await self._collection.replace_one({"_id": task_id}, doc, upsert=True)

    def load(self, task_id: str) -> dict | None:
        return _run_async(self._load_async(task_id))

    async def _load_async(self, task_id: str) -> dict | None:
        doc = await self._collection.find_one({"_id": task_id})
        return _task_dict_from_doc(doc) if doc is not None else None

    def list_all(self) -> list[dict]:
        return _run_async(self._list_all_async())

    async def _list_all_async(self) -> list[dict]:
        cursor = self._collection.find().sort("created_at", -1)
        return [_task_dict_from_doc(doc) for doc in await cursor.to_list(length=1000)]

    def update_status(
        self, task_id: str, status: str, status_history: list[str],
        topic_ids: list[str], drafted_topic_ids: list[str],
    ) -> None:
        _run_async(self._update_status_async(
            task_id, status, status_history, topic_ids, drafted_topic_ids,
        ))

    async def _update_status_async(
        self, task_id: str, status: str, status_history: list[str],
        topic_ids: list[str], drafted_topic_ids: list[str],
    ) -> None:
        await self._collection.update_one(
            {"_id": task_id},
            {"$set": {
                "status": status, "status_history": status_history,
                "topic_ids": topic_ids, "drafted_topic_ids": drafted_topic_ids,
                "updated_at": _iso_now(),
            }},
        )


def _task_dict_from_doc(doc: dict[str, Any]) -> dict:
    return {
        "id": doc["_id"], "status": doc["status"],
        "status_history": doc.get("status_history", []),
        "mode": doc.get("mode", "demo"),
        "collected_article_count": doc.get("collected_article_count", 0),
        "window_days": doc.get("window_days", 0),
        "expanded_categories": doc.get("expanded_categories", []),
        "collection_warnings": doc.get("collection_warnings", []),
        "source_counts": doc.get("source_counts", {}),
        "topic_ids": doc.get("topic_ids", []),
        "drafted_topic_ids": doc.get("drafted_topic_ids", []),
        "result_json": doc.get("result_json"),
        "created_at": doc.get("created_at", ""),
        "updated_at": doc.get("updated_at", ""),
    }


# ---------------------------------------------------------------------------
# Evidence repository
# ---------------------------------------------------------------------------

class MongoEvidenceRepository:
    """Persist evidence packs keyed by topic id."""

    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self._collection = db.evidence_packs

    def save(self, topic_id: str, task_id: str, pack: EvidencePack) -> None:
        _run_async(self._save_async(topic_id, task_id, pack))

    async def _save_async(self, topic_id: str, task_id: str, pack: EvidencePack) -> None:
        doc = {
            "_id": topic_id, "task_id": task_id,
            "pack_json": pack.model_dump_json(),
            "created_at": _iso_now(),
        }
        await self._collection.replace_one({"_id": topic_id}, doc, upsert=True)

    def load(self, topic_id: str) -> EvidencePack | None:
        return _run_async(self._load_async(topic_id))

    async def _load_async(self, topic_id: str) -> EvidencePack | None:
        doc = await self._collection.find_one({"_id": topic_id})
        return EvidencePack.model_validate_json(doc["pack_json"]) if doc is not None else None


# ---------------------------------------------------------------------------
# Draft repository
# ---------------------------------------------------------------------------

class MongoDraftRepository:
    """Persist draft records with full version history."""

    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self._collection = db.drafts

    def save(
        self, draft_id: str, topic_id: str, task_id: str,
        document: DraftDocument, selected_title: str, version: int,
        trusted_body: str, trusted_titles: tuple[str, ...],
        quality_issues: list[QualityIssue], versions: list[DraftDocument],
    ) -> None:
        _run_async(self._save_async(
            draft_id, topic_id, task_id, document, selected_title,
            version, trusted_body, trusted_titles, quality_issues, versions,
        ))

    async def _save_async(
        self, draft_id: str, topic_id: str, task_id: str,
        document: DraftDocument, selected_title: str, version: int,
        trusted_body: str, trusted_titles: tuple[str, ...],
        quality_issues: list[QualityIssue], versions: list[DraftDocument],
    ) -> None:
        now = _iso_now()
        doc = {
            "_id": draft_id, "topic_id": topic_id, "task_id": task_id,
            "document_json": document.model_dump_json(),
            "selected_title": selected_title, "version": version,
            "trusted_body": trusted_body,
            "trusted_titles": list(trusted_titles),
            "quality_issues_json": json.dumps(
                [i.model_dump() for i in quality_issues], ensure_ascii=False,
            ),
            "versions_json": json.dumps(
                [v.model_dump(mode="json") for v in versions], ensure_ascii=False,
            ),
            "created_at": now, "updated_at": now,
        }
        await self._collection.replace_one({"_id": draft_id}, doc, upsert=True)

    def load(self, draft_id: str) -> dict | None:
        return _run_async(self._load_async(draft_id))

    async def _load_async(self, draft_id: str) -> dict | None:
        doc = await self._collection.find_one({"_id": draft_id})
        return _draft_dict_from_doc(doc) if doc is not None else None

    def list_by_task(self, task_id: str) -> list[dict]:
        return _run_async(self._list_by_task_async(task_id))

    async def _list_by_task_async(self, task_id: str) -> list[dict]:
        cursor = self._collection.find({"task_id": task_id}).sort("created_at", -1)
        return [_draft_dict_from_doc(doc) for doc in await cursor.to_list(length=1000)]


def _draft_dict_from_doc(doc: dict[str, Any]) -> dict:
    return {
        "id": doc["_id"], "topic_id": doc["topic_id"], "task_id": doc["task_id"],
        "document": DraftDocument.model_validate_json(doc["document_json"]),
        "selected_title": doc["selected_title"], "version": doc["version"],
        "trusted_body": doc["trusted_body"],
        "trusted_titles": tuple(doc["trusted_titles"]),
        "quality_issues": [
            QualityIssue.model_validate(item)
            for item in json.loads(doc.get("quality_issues_json", "[]"))
        ],
        "versions": [
            DraftDocument.model_validate(item)
            for item in json.loads(doc["versions_json"])
        ],
        "created_at": doc.get("created_at", ""),
        "updated_at": doc.get("updated_at", ""),
    }


class MongoCreatorProjectRepository:
    """Persist creator project workflow state in MongoDB."""

    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self._collection = db.creator_projects

    def save(self, project: CreatorProject) -> None:
        _run_async(self._save_async(project))

    async def _save_async(self, project: CreatorProject) -> None:
        await self._collection.replace_one(
            {"_id": project.id},
            {
                "_id": project.id,
                "topic_id": project.topic_id,
                "task_id": project.task_id,
                "project_json": project.model_dump_json(),
                "created_at": project.created_at.isoformat(),
                "updated_at": project.updated_at.isoformat(),
            },
            upsert=True,
        )

    def get(self, project_id: str) -> CreatorProject | None:
        return _run_async(self._get_async(project_id))

    async def _get_async(self, project_id: str) -> CreatorProject | None:
        doc = await self._collection.find_one({"_id": project_id})
        return (
            CreatorProject.model_validate_json(doc["project_json"])
            if doc is not None
            else None
        )

    def get_by_topic(self, topic_id: str) -> CreatorProject | None:
        return _run_async(self._get_by_topic_async(topic_id))

    async def _get_by_topic_async(self, topic_id: str) -> CreatorProject | None:
        doc = await self._collection.find_one({"topic_id": topic_id})
        return (
            CreatorProject.model_validate_json(doc["project_json"])
            if doc is not None
            else None
        )

    def list_all(self) -> list[CreatorProject]:
        return _run_async(self._list_all_async())

    async def _list_all_async(self) -> list[CreatorProject]:
        cursor = self._collection.find().sort("updated_at", -1)
        docs = await cursor.to_list(length=1000)
        return [
            CreatorProject.model_validate_json(doc["project_json"])
            for doc in docs
        ]


class MongoAccountProfileRepository:
    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self._collection = db.account_profiles

    def save(self, profile: AccountProfile) -> None:
        _run_async(self._collection.replace_one(
            {"_id": f"{profile.id}:v{profile.version}"},
            {"_id": f"{profile.id}:v{profile.version}", "profile_id": profile.id,
             "version": profile.version, "profile_json": profile.model_dump_json()},
            upsert=True,
        ))

    def get_current(self, profile_id: str = "local-default") -> AccountProfile | None:
        return _run_async(self._get_current_async(profile_id))

    async def _get_current_async(self, profile_id: str) -> AccountProfile | None:
        doc = await self._collection.find_one({"profile_id": profile_id}, sort=[("version", -1)])
        return AccountProfile.model_validate_json(doc["profile_json"]) if doc else None

    def list_versions(self, profile_id: str = "local-default") -> list[AccountProfile]:
        return _run_async(self._list_versions_async(profile_id))

    async def _list_versions_async(self, profile_id: str) -> list[AccountProfile]:
        docs = await self._collection.find({"profile_id": profile_id}).sort("version", -1).to_list(100)
        return [AccountProfile.model_validate_json(doc["profile_json"]) for doc in docs]


class MongoContentOpportunityRepository:
    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self._collection = db.content_opportunities

    def save(self, opportunity: ContentOpportunity) -> None:
        _run_async(self._collection.replace_one(
            {"_id": opportunity.id},
            {"_id": opportunity.id, "source_id": opportunity.source_id,
             "creation_score": opportunity.creation_score,
             "opportunity_json": opportunity.model_dump_json()},
            upsert=True,
        ))

    def get(self, opportunity_id: str) -> ContentOpportunity | None:
        return _run_async(self._get_async(opportunity_id))

    async def _get_async(self, opportunity_id: str) -> ContentOpportunity | None:
        doc = await self._collection.find_one({"_id": opportunity_id})
        return ContentOpportunity.model_validate_json(doc["opportunity_json"]) if doc else None

    def get_by_source(self, source_id: str) -> ContentOpportunity | None:
        return _run_async(self._get_by_source_async(source_id))

    async def _get_by_source_async(self, source_id: str) -> ContentOpportunity | None:
        doc = await self._collection.find_one({"source_id": source_id})
        return ContentOpportunity.model_validate_json(doc["opportunity_json"]) if doc else None

    def list_all(self) -> list[ContentOpportunity]:
        return _run_async(self._list_all_async())

    async def _list_all_async(self) -> list[ContentOpportunity]:
        docs = await self._collection.find().sort("creation_score", -1).to_list(1000)
        return [ContentOpportunity.model_validate_json(doc["opportunity_json"]) for doc in docs]


class MongoGenerationAuditRepository:
    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self._prompts = db.prompt_versions
        self._calls = db.llm_call_records
        self._replays = db.creation_replay_reports

    def save_prompt_version(self, prompt: PromptVersion) -> None:
        _run_async(self._prompts.replace_one(
            {"_id": prompt.id}, {"_id": prompt.id, "stage": prompt.stage,
             "version": prompt.version, "json": prompt.model_dump_json()}, upsert=True
        ))

    def list_prompt_versions(self) -> list[PromptVersion]:
        return _run_async(self._list_prompts_async())

    async def _list_prompts_async(self) -> list[PromptVersion]:
        docs = await self._prompts.find().sort([("stage", 1), ("version", -1)]).to_list(100)
        return [PromptVersion.model_validate_json(doc["json"]) for doc in docs]

    def save_call(self, call: LlmCallRecord) -> None:
        _run_async(self._calls.replace_one(
            {"_id": call.id}, {"_id": call.id, "created_at": call.created_at.isoformat(),
             "project_id": call.project_id, "json": call.model_dump_json()}, upsert=True
        ))

    def list_calls(self, limit: int = 100) -> list[LlmCallRecord]:
        return _run_async(self._list_calls_async(limit))

    async def _list_calls_async(self, limit: int) -> list[LlmCallRecord]:
        docs = await self._calls.find().sort("created_at", -1).to_list(limit)
        return [LlmCallRecord.model_validate_json(doc["json"]) for doc in docs]

    def save_replay(self, report: CreationReplayReport) -> None:
        _run_async(self._replays.replace_one(
            {"_id": report.id}, {"_id": report.id, "created_at": report.created_at.isoformat(),
             "json": report.model_dump_json()}, upsert=True
        ))

    def latest_replay(self) -> CreationReplayReport | None:
        values = self.list_replays(1)
        return values[0] if values else None

    def list_replays(self, limit: int = 20) -> list[CreationReplayReport]:
        return _run_async(self._list_replays_async(limit))

    async def _list_replays_async(self, limit: int) -> list[CreationReplayReport]:
        docs = await self._replays.find().sort("created_at", -1).to_list(limit)
        return [CreationReplayReport.model_validate_json(doc["json"]) for doc in docs]
