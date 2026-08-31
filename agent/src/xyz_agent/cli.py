from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, date, datetime

import structlog

from .config import Settings
from .models import Evidence, Publication, Review, Update
from .publish import Publisher
from .runtime import FakeRuntime, PiRuntime, SessionResult
from .storage import LocalStorage, S3Storage, Storage
from .workflow import DigestWorkflow


def storage_from_settings(settings: Settings) -> Storage:
    if settings.storage == "local":
        return LocalStorage(settings.storage_root)
    assert settings.s3_bucket and settings.s3_access_key_id and settings.s3_secret_access_key
    return S3Storage(
        settings.s3_bucket,
        endpoint_url=settings.s3_endpoint_url,
        region=settings.s3_region,
        access_key_id=settings.s3_access_key_id,
        secret_access_key=settings.s3_secret_access_key,
    )


def fake_runtime(settings: Settings, target: date) -> FakeRuntime:
    source = settings.registry().sources[0]
    evidence = Evidence(
        id="ev_0123456789abcdef",
        source_id=source.id,
        url=source.feed_url,
        title="Fixture update",
        text="A deterministic fixture entry used for local workflow verification.",
        kind="entry",
    )
    publication = Publication(
        candidate_id="fixture-candidate",
        publication_date=target,
        title="Today in software",
        introduction="A small selection of links worth a closer look today.",
        updates=[
            Update(
                heading="A useful engineering note",
                description="A new note considers a practical software-development concern.",
                why_read="It may offer a clear perspective for working engineers.",
                evidence_ids=[evidence.id],
            )
        ],
    )
    review = Review(
        candidate_id=publication.candidate_id,
        approved=True,
        rationale="The concise annotation is supported and original.",
    )
    return FakeRuntime(
        producer=[SessionResult(terminal=publication, evidence=[evidence])],
        reviewer=[SessionResult(terminal=review)],
    )


async def run(target: date | None) -> int:
    settings = Settings()
    target = target or datetime.now(UTC).date()
    storage = storage_from_settings(settings)
    runtime = fake_runtime(settings, target) if settings.runtime == "fake" else PiRuntime(settings)
    workflow = DigestWorkflow(
        settings,
        runtime,
        Publisher(storage, retention_days=settings.retention_days),
    )
    result = await workflow.run(target)
    print(result.model_dump_json())
    return 0 if result.status in ("published", "contended") else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate and publish the daily digest")
    parser.add_argument("--date", type=date.fromisoformat, help="UTC publication date")
    parser.add_argument("--pretty-logs", action="store_true")
    args = parser.parse_args()
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ]
    )
    try:
        raise SystemExit(asyncio.run(run(args.date)))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"event": "configuration_error", "error": str(exc)}), file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
