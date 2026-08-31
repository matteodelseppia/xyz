from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .models import Artifact, Evidence, Manifest, Publication, RenderedSet, SourceRegistry

TEMPLATE_VERSION = "1"
VALIDATION_RULES = [
    "evidence-resolves",
    "originality",
    "safe-html",
    "artifact-integrity",
    "navigation-resolves",
    "output-bounds",
]


@dataclass(frozen=True)
class ResolvedUpdate:
    heading: str
    description: str
    why_read: str
    evidence_ids: list[str]
    url: str
    source_name: str


class Renderer:
    def __init__(self, template_dir: Path) -> None:
        self.template_dir = template_dir
        self.environment = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=True,
            undefined=StrictUndefined,
        )

    def render(
        self,
        publication: Publication,
        evidence: list[Evidence],
        registry: SourceRegistry,
        *,
        run_id: str,
        review_status: str,
        retained_dates: list[date],
        provenance: dict[str, str],
    ) -> RenderedSet:
        evidence_by_id = {item.id: item for item in evidence}
        sources_by_id = {source.id: source for source in registry.sources}
        resolved: list[ResolvedUpdate] = []
        for update in publication.updates:
            primary = evidence_by_id[update.evidence_ids[0]]
            source = sources_by_id[primary.source_id]
            resolved.append(
                ResolvedUpdate(
                    heading=update.heading,
                    description=update.description,
                    why_read=update.why_read,
                    evidence_ids=update.evidence_ids,
                    url=str(primary.url),
                    source_name=source.name,
                )
            )

        css = (self.template_dir / "site.css").read_bytes()
        css_hash = hashlib.sha256(css).hexdigest()
        asset_path = f"assets/{css_hash}.css"
        public_asset_url = f"/assets/{publication.publication_date}/{run_id}/{css_hash}.css"
        ordered = sorted(set(retained_dates + [publication.publication_date]))
        position = ordered.index(publication.publication_date)
        previous_date = ordered[position - 1] if position else None
        next_date = ordered[position + 1] if position + 1 < len(ordered) else None
        html = (
            self.environment.get_template("day.html.j2")
            .render(
                publication=publication,
                updates=resolved,
                asset_url=public_asset_url,
                asset_integrity=base64.b64encode(bytes.fromhex(css_hash)).decode(),
                previous_date=previous_date,
                next_date=next_date,
            )
            .encode()
        )

        public_document = {
            "schema_version": 1,
            "candidate_id": publication.candidate_id,
            "publication_date": publication.publication_date.isoformat(),
            "title": publication.title,
            "introduction": publication.introduction,
            "updates": [
                {
                    "heading": update.heading,
                    "description": update.description,
                    "why_read": update.why_read,
                    "source_url": update.url,
                    "source_name": update.source_name,
                }
                for update in resolved
            ],
            "provenance": provenance,
            "review_status": review_status,
        }
        publication_json = json.dumps(
            public_document, sort_keys=True, separators=(",", ":")
        ).encode()
        files: dict[str, tuple[bytes, str]] = {
            "index.html": (html, "text/html; charset=utf-8"),
            "publication.json": (publication_json, "application/json"),
            asset_path: (css, "text/css; charset=utf-8"),
        }
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
            review_status=review_status,  # type: ignore[arg-type]
            artifacts=artifacts,
            provenance=provenance,
            validation_rules=VALIDATION_RULES,
        )
        manifest_data = manifest.model_dump_json(exclude_none=True).encode()
        files["manifest.json"] = (manifest_data, "application/json")
        return RenderedSet(files=files, manifest=manifest)
