from __future__ import annotations

import re
from collections import Counter
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.parse import urlparse

from .models import Evidence, Finding, FindingCategory, Publication, RenderedSet

VALIDATION_RULES = [
    "evidence-resolves",
    "source-diversity",
    "originality",
    "safe-html",
    "artifact-integrity",
    "navigation-resolves",
    "output-bounds",
]
TOKEN_RE = re.compile(r"[\w'-]+", re.UNICODE)


class DigestHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.hrefs: list[str] = []
        self.title_depth = 0
        self.title_text = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        values = dict(attrs)
        if tag == "title":
            self.title_depth += 1
        for name in ("href", "src"):
            if values.get(name):
                self.hrefs.append(values[name] or "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.title_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.title_depth:
            self.title_text += data


def _words(text: str) -> list[str]:
    return [token.casefold() for token in TOKEN_RE.findall(text)]


def _originality_matches(
    publication: Publication, evidence: list[Evidence], size: int = 8
) -> dict[str, list[str]]:
    components: list[tuple[str, str, str]] = [("publication", "title", publication.title)]
    # Article headings are required to match source titles and are rendered from evidence.
    for index, update in enumerate(publication.updates, start=1):
        target = f"update {index}"
        components.extend(
            (
                (target, "description", update.description),
                (target, "why_read", update.why_read),
                *(
                    (
                        target,
                        f"{verification.assertion} verification question",
                        verification.question,
                    )
                    for verification in update.verifications
                ),
            )
        )

    generated_windows: dict[tuple[str, ...], set[tuple[str, str]]] = {}
    for target, field, text in components:
        words = _words(text)
        for index in range(len(words) - size + 1):
            window = tuple(words[index : index + size])
            generated_windows.setdefault(window, set()).add((target, field))

    matched_components: set[tuple[str, str]] = set()
    for item in evidence:
        words = _words(item.text)
        for index in range(len(words) - size + 1):
            matched_components.update(generated_windows.get(tuple(words[index : index + size]), ()))

    grouped: dict[str, list[str]] = {}
    for target, field, _ in components:
        if (target, field) in matched_components:
            grouped.setdefault(target, []).append(field)
    return grouped


class Validator:
    def validate(
        self,
        publication: Publication,
        evidence: list[Evidence],
        rendered: RenderedSet,
        *,
        retained_dates: list[str],
    ) -> list[Finding]:
        findings: list[Finding] = []
        evidence_by_id = {item.id: item for item in evidence}
        for index, update in enumerate(publication.updates):
            unknown = [
                item
                for item in [
                    *update.evidence_ids,
                    *(
                        evidence_id
                        for check in update.verifications
                        for evidence_id in check.evidence_ids
                    ),
                ]
                if item not in evidence_by_id
            ]
            if unknown:
                findings.append(
                    Finding(
                        category=FindingCategory.INTEGRITY,
                        affected_content=f"update {index + 1}",
                        correction=f"Replace unknown evidence references: {', '.join(unknown)}.",
                        rule="evidence-resolves",
                    )
                )
        if len(publication.updates) >= 10:
            primary_sources = [
                evidence_by_id[update.evidence_ids[0]].source_id
                for update in publication.updates
                if update.evidence_ids[0] in evidence_by_id
            ]
            source_counts = Counter(primary_sources)
            if len(source_counts) < 8:
                findings.append(
                    Finding(
                        category=FindingCategory.DUPLICATION,
                        affected_content="digest source mix",
                        correction=(
                            f"Use at least 8 distinct configured sources for 10 updates; "
                            f"the candidate uses {len(source_counts)}. "
                            "Replace concentrated or weak items."
                        ),
                        rule="source-diversity",
                    )
                )
            repeated = sorted(source for source, count in source_counts.items() if count > 2)
            if repeated:
                findings.append(
                    Finding(
                        category=FindingCategory.DUPLICATION,
                        affected_content="repeated sources",
                        correction=(
                            "Use at most two updates from any source, and only when they are "
                            f"clearly distinct and unusually important: {', '.join(repeated)}."
                        ),
                        rule="source-diversity",
                    )
                )
        for target, fields in _originality_matches(publication, evidence).items():
            findings.append(
                Finding(
                    category=FindingCategory.ORIGINALITY,
                    affected_content=f"{target}: {', '.join(fields)}",
                    correction=(
                        "Rewrite every listed field in original, higher-level language; exact "
                        "source wording is allowed only for the linked article heading."
                    ),
                    rule="originality",
                )
            )

        html_data = rendered.files.get("index.html", (b"", ""))[0]
        if len(html_data) > 200_000 or len(publication.updates) > 12:
            findings.append(
                Finding(
                    category=FindingCategory.INTEGRITY,
                    affected_content="rendered page",
                    correction="Reduce the digest to fit output bounds.",
                    rule="output-bounds",
                )
            )
        parser = DigestHTMLParser()
        try:
            parser.feed(html_data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            findings.append(
                Finding(
                    category=FindingCategory.INTEGRITY,
                    affected_content="HTML document",
                    correction=f"Produce parseable UTF-8 HTML ({exc}).",
                    rule="safe-html",
                )
            )
            return findings
        forbidden = sorted(set(parser.tags) & {"iframe", "object", "embed", "form"})
        if forbidden:
            findings.append(
                Finding(
                    category=FindingCategory.SECURITY,
                    affected_content="HTML tags",
                    correction=f"Remove forbidden tags: {', '.join(forbidden)}.",
                    rule="safe-html",
                )
            )
        if publication.title not in parser.title_text:
            findings.append(
                Finding(
                    category=FindingCategory.INTEGRITY,
                    affected_content="document title",
                    correction="Include the publication title in the HTML title.",
                    rule="safe-html",
                )
            )
        allowed_days = set(retained_dates) | {publication.publication_date.isoformat()}
        for href in parser.hrefs:
            parsed = urlparse(href)
            if parsed.scheme and parsed.scheme not in ("http", "https"):
                findings.append(
                    Finding(
                        category=FindingCategory.SECURITY,
                        affected_content=href[:150],
                        correction="Use only HTTPS/HTTP source links and site-relative links.",
                        rule="safe-html",
                    )
                )
            match = re.fullmatch(r"/days/(\d{4}-\d{2}-\d{2})/", href)
            if match and match.group(1) not in allowed_days:
                findings.append(
                    Finding(
                        category=FindingCategory.INTEGRITY,
                        affected_content=href,
                        correction="Link only to retained publication dates.",
                        rule="navigation-resolves",
                    )
                )
        for artifact in rendered.manifest.artifacts:
            path = PurePosixPath(artifact.path)
            data = rendered.files.get(artifact.path, (b"", ""))[0]
            if path.is_absolute() or ".." in path.parts or not data:
                findings.append(
                    Finding(
                        category=FindingCategory.SECURITY,
                        affected_content=artifact.path,
                        correction="Use a non-empty artifact under the run prefix.",
                        rule="artifact-integrity",
                    )
                )
        return findings
