"""Bounded operational CLI for the Story shadow engine."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Sequence

from wechat_agent.storage.sqlite import (
    SqliteArticleRepository,
    SqliteCurationDecisionRepository,
    SqliteSourceRepository,
)
from wechat_agent.storage.story_sqlite import SqliteStoryRepository
from wechat_agent.story.audit import build_story_shadow_audit
from wechat_agent.story.sampling import select_annotation_candidates
from wechat_agent.story.service import StoryShadowService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m wechat_agent.story")
    commands = parser.add_subparsers(dest="command", required=True)
    replay = commands.add_parser("replay", help="Replay a bounded Story window")
    replay.add_argument("--database-url", required=True)
    replay.add_argument("--days", type=int, required=True)
    export = commands.add_parser(
        "export-pairs",
        help="Export suggestion-blind Story annotation candidates",
    )
    export.add_argument("--database-url", required=True)
    export.add_argument("--output", required=True)
    export.add_argument("--limit", type=int, default=50)
    export.add_argument("--force", action="store_true")
    audit = commands.add_parser(
        "audit",
        help="Write a structural Story shadow quality report",
    )
    audit.add_argument("--database-url", required=True)
    audit.add_argument("--output", required=True)
    audit.add_argument("--window-hours", type=int, default=168)
    audit.add_argument("--max-story-reports", type=int, default=8)
    audit.add_argument("--max-run-gap-hours", type=int, default=24)
    audit.add_argument("--generated-at")
    audit.add_argument("--force", action="store_true")
    return parser


def _parse_timestamp(value: str | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("--generated-at must include a timezone")
    return parsed.astimezone(UTC)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return int(error.code)

    if args.command == "replay":
        if not 1 <= args.days <= 7:
            print("replay days must be between 1 and 7", file=sys.stderr)
            return 2
        article_repository = SqliteArticleRepository(args.database_url)
        curation_repository = SqliteCurationDecisionRepository(
            article_repository.engine
        )
        source_repository = SqliteSourceRepository(article_repository.engine)
        story_repository = SqliteStoryRepository(article_repository.engine)
        service = StoryShadowService(
            article_repository,
            curation_repository,
            source_repository,
            story_repository,
        )
        run = service.replay(args.days, datetime.now(UTC))
        print(run.model_dump_json(by_alias=True))
        return 0

    if args.command == "export-pairs":
        if not 1 <= args.limit <= 500:
            print("export limit must be between 1 and 500", file=sys.stderr)
            return 2
        output_path = Path(args.output)
        if output_path.exists() and not args.force:
            print(f"output already exists: {output_path}", file=sys.stderr)
            return 2

        article_repository = SqliteArticleRepository(args.database_url)
        story_repository = SqliteStoryRepository(article_repository.engine)
        source_repository = SqliteSourceRepository(article_repository.engine)
        pairs = []
        offset = 0
        while True:
            page = story_repository.list_pairs(
                suggestion=None,
                offset=offset,
                limit=100,
            )
            pairs.extend(page.items)
            offset += len(page.items)
            if offset >= page.total or not page.items:
                break
        publisher_groups = {
            source.id: source.publisher_group
            for source in source_repository.list_all()
        }
        rows = select_annotation_candidates(
            pairs,
            article_repository.list_all(limit=10000),
            limit=args.limit,
            publisher_groups=publisher_groups,
        )
        content = "".join(
            f"{row.model_dump_json(by_alias=True)}\n" for row in rows
        ).encode("utf-8")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(content)
        print(
            json.dumps(
                {
                    "exportedCount": len(rows),
                    "sha256": sha256(content).hexdigest(),
                    "output": str(output_path),
                },
                ensure_ascii=False,
            )
        )
        return 0

    if args.command == "audit":
        if args.window_hours < 1:
            print("audit window-hours must be at least 1", file=sys.stderr)
            return 2
        output_path = Path(args.output)
        if output_path.exists() and not args.force:
            print(f"output already exists: {output_path}", file=sys.stderr)
            return 2
        try:
            generated_at = _parse_timestamp(args.generated_at)
        except ValueError as error:
            print(str(error), file=sys.stderr)
            return 2

        article_repository = SqliteArticleRepository(args.database_url)
        story_repository = SqliteStoryRepository(article_repository.engine)
        report = build_story_shadow_audit(
            story_repository,
            generated_at=generated_at,
            window_hours=args.window_hours,
            max_story_reports=args.max_story_reports,
            max_run_gap_hours=args.max_run_gap_hours,
        )
        content = report.model_dump_json(by_alias=True).encode("utf-8") + b"\n"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(content)
        print(
            json.dumps(
                {
                    "status": report.status,
                    "storyCount": report.story_count,
                    "pairCount": report.pair_count,
                    "sha256": sha256(content).hexdigest(),
                    "output": str(output_path),
                },
                ensure_ascii=False,
            )
        )
        return 0 if report.status == "passing" else 2

    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
