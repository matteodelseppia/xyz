from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .models import (
    Artifact,
    Evidence,
    Manifest,
    Publication,
    RenderedSet,
    Review,
    ReviewCheck,
    SourceRegistry,
)

TEMPLATE_VERSION = "6"
VALIDATION_RULES = [
    "evidence-resolves",
    "originality",
    "safe-html",
    "artifact-integrity",
    "navigation-resolves",
    "output-bounds",
]
CRON_DAILY = re.compile(r"^(?P<minute>\d{1,2}) (?P<hour>\d{1,2}) \* \* \*$")


def _article_url(url: str) -> str:
    """Normalize an article URL enough to join feed and entry evidence."""
    parsed = urlsplit(url)
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, parsed.query, ""))


def next_cron_run(now: datetime, schedule: str) -> datetime:
    """Return the first UTC run strictly after ``now`` for a daily cron."""
    match = CRON_DAILY.fullmatch(schedule)
    if not match:
        raise ValueError(f"Unsupported scheduled-run cron: {schedule}")
    minute, hour = (int(match.group(name)) for name in ("minute", "hour"))
    if minute > 59 or hour > 23:
        raise ValueError(f"Unsupported scheduled-run cron: {schedule}")

    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return candidate if candidate > now else candidate + timedelta(days=1)


@dataclass(frozen=True)
class ResolvedVerification:
    assertion: str
    question: str
    sources: list[dict[str, str]]


@dataclass(frozen=True)
class ResolvedReviewCheck:
    evidence_supported: bool
    attribution_correct: bool
    selection_rationale_credible: bool
    opinion: str
    uncertainty: str | None


@dataclass(frozen=True)
class ResolvedUpdate:
    heading: str
    published_at: datetime | None
    description: str
    why_read: str
    keywords: list[str]
    evidence_ids: list[str]
    url: str
    source_name: str
    verifications: list[ResolvedVerification]
    additional_verification_sources: list[dict[str, str]]
    review: ResolvedReviewCheck


class Renderer:
    def __init__(self, template_dir: Path) -> None:
        self.template_dir = template_dir
        self.environment = Environment(
            loader=FileSystemLoader(template_dir), autoescape=True, undefined=StrictUndefined
        )

    def render(
        self,
        publication: Publication,
        evidence: list[Evidence],
        registry: SourceRegistry,
        *,
        review: Review | None = None,
        editorial_anchors: list[dict[str, Any]] | None = None,
        run_id: str,
        review_status: Literal["approved", "iteration_limit"],
        retained_dates: list[date],
        provenance: dict[str, str],
        transcript: list[dict[str, Any]] | None = None,
        scheduled_run_cron: str = "0 6 * * *",
    ) -> RenderedSet:
        evidence_by_id = {item.id: item for item in evidence}
        sources_by_id = {source.id: source for source in registry.sources}
        updates: list[ResolvedUpdate] = []
        checks_by_index = {item.update_index: item for item in review.checks} if review else {}
        editorial_anchors = editorial_anchors or []
        for update_index, update in enumerate(publication.updates):
            primary = evidence_by_id[update.evidence_ids[0]]
            primary_url = _article_url(str(primary.url))
            selected = [evidence_by_id[evidence_id] for evidence_id in update.evidence_ids]
            related = [
                item
                for item in evidence
                if item.source_id == primary.source_id
                and _article_url(str(item.url)) == primary_url
                and item.id not in update.evidence_ids
            ]
            published_at = next(
                (
                    item.published_at
                    for item in [*selected, *related]
                    if item.published_at is not None
                ),
                None,
            )
            resolved_verifications: list[ResolvedVerification] = []
            for verification in update.verifications:
                verification_sources: list[dict[str, str]] = []
                seen_urls: set[str] = set()
                for evidence_id in verification.evidence_ids:
                    item = evidence_by_id[evidence_id]
                    url = str(item.url)
                    if url not in seen_urls:
                        verification_sources.append(
                            {"url": url, "source_name": sources_by_id[item.source_id].name}
                        )
                        seen_urls.add(url)
                resolved_verifications.append(
                    ResolvedVerification(
                        assertion=verification.assertion.replace("_", " "),
                        question=verification.question,
                        sources=verification_sources,
                    )
                )
            additional_verification_sources: list[dict[str, str]] = []
            seen_verification_urls = {str(primary.url)}
            for resolved_verification in resolved_verifications:
                for source in resolved_verification.sources:
                    if source["url"] not in seen_verification_urls:
                        additional_verification_sources.append(source)
                        seen_verification_urls.add(source["url"])
            item_check = checks_by_index.get(
                update_index,
                ReviewCheck(
                    update_index=update_index,
                    evidence_supported=False,
                    attribution_correct=False,
                    selection_rationale_credible=False,
                    opinion="Independent item review was unavailable.",
                    uncertainty="Independent item review was unavailable.",
                ),
            )
            updates.append(
                ResolvedUpdate(
                    heading=primary.title,
                    published_at=published_at,
                    description=update.description,
                    why_read=update.why_read,
                    keywords=update.keywords,
                    evidence_ids=update.evidence_ids,
                    url=str(primary.url),
                    source_name=sources_by_id[primary.source_id].name,
                    verifications=resolved_verifications,
                    additional_verification_sources=additional_verification_sources,
                    review=ResolvedReviewCheck(
                        evidence_supported=item_check.evidence_supported,
                        attribution_correct=item_check.attribution_correct,
                        selection_rationale_credible=item_check.selection_rationale_credible,
                        opinion=item_check.opinion,
                        uncertainty=item_check.uncertainty,
                    ),
                )
            )

        generated_at = datetime.now(UTC)
        next_run = next_cron_run(generated_at, scheduled_run_cron)
        css = (self.template_dir / "site.css").read_bytes()
        icon = (self.template_dir / "favicon.svg").read_bytes()
        script = (self.template_dir / "countdown.js").read_bytes()
        assets = {"css": (css, "css"), "icon": (icon, "svg"), "script": (script, "js")}
        hashes = {name: hashlib.sha256(data).hexdigest() for name, (data, _) in assets.items()}
        asset_urls = {
            name: f"/assets/{publication.publication_date}/{run_id}/{digest}.{extension}"
            for name, digest in hashes.items()
            for _, extension in [assets[name]]
        }
        common = {
            "publication": publication,
            "updates": updates,
            "asset_urls": asset_urls,
            "asset_integrities": {
                name: base64.b64encode(bytes.fromhex(digest)).decode()
                for name, digest in hashes.items()
            },
            "generated_at": generated_at,
            "next_run": next_run,
        }
        html = self.environment.get_template("day.html.j2").render(**common).encode()
        prompt_html = (
            self.environment.get_template("prompt.html.j2")
            .render(
                **common,
                transcript_json=json.dumps(transcript or [], indent=2, sort_keys=True),
            )
            .encode()
        )
        sources_html = (
            self.environment.get_template("sources.html.j2")
            .render(
                **common,
                sources=registry.sources,
            )
            .encode()
        )
        public_document = {
            "schema_version": 1,
            "candidate_id": publication.candidate_id,
            "publication_date": publication.publication_date.isoformat(),
            "title": publication.title,
            "generated_at": generated_at.isoformat(),
            "updates": [
                {
                    "heading": update.heading,
                    "published_at": update.published_at.isoformat()
                    if update.published_at
                    else None,
                    "description": update.description,
                    "why_read": update.why_read,
                    "keywords": update.keywords,
                    "source_url": update.url,
                    "source_name": update.source_name,
                    "verifications": [
                        {
                            "assertion": verification.assertion,
                            "question": verification.question,
                            "sources": verification.sources,
                        }
                        for verification in update.verifications
                    ],
                    "review": {
                        "evidence_supported": update.review.evidence_supported,
                        "attribution_correct": update.review.attribution_correct,
                        "selection_rationale_credible": update.review.selection_rationale_credible,
                        "opinion": update.review.opinion,
                        "uncertainty": update.review.uncertainty,
                    },
                }
                for update in updates
            ],
            "editorial_calibration": {
                "name": "loved ones",
                "url": "/loved-ones/",
                "anchors": [
                    {"url": item["url"], "tags": item["tags"]} for item in editorial_anchors
                ],
            },
            "provenance": provenance,
            "review_status": review_status,
        }
        files: dict[str, tuple[bytes, str]] = {
            "index.html": (html, "text/html; charset=utf-8"),
            "prompt/index.html": (prompt_html, "text/html; charset=utf-8"),
            "sources/index.html": (sources_html, "text/html; charset=utf-8"),
            "publication.json": (
                json.dumps(public_document, sort_keys=True, separators=(",", ":")).encode(),
                "application/json",
            ),
        }
        for name, (data, extension) in assets.items():
            content_type = {
                "css": "text/css; charset=utf-8",
                "svg": "image/svg+xml",
                "js": "application/javascript; charset=utf-8",
            }[extension]
            files[f"assets/{hashes[name]}.{extension}"] = (data, content_type)
        artifacts = [
            Artifact(
                path=path,
                sha256=hashlib.sha256(data).hexdigest(),
                size=len(data),
                content_type=content_type,
            )
            for path, (data, content_type) in sorted(files.items())
        ]
        manifest = Manifest(
            run_id=run_id,
            publication_date=publication.publication_date,
            created_at=generated_at,
            review_status=review_status,
            artifacts=artifacts,
            provenance=provenance,
            validation_rules=VALIDATION_RULES,
        )
        files["manifest.json"] = (
            manifest.model_dump_json(exclude_none=True).encode(),
            "application/json",
        )
        return RenderedSet(files=files, manifest=manifest)
