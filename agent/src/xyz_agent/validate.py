from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.parse import urlparse

from .models import Evidence, Finding, FindingCategory, Publication, RenderedSet

VALIDATION_RULES = [
    "evidence-resolves",
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


def _overlap(generated: str, evidence: str, size: int = 8) -> str | None:
    left = _words(generated)
    right = _words(evidence)
    if len(left) < size or len(right) < size:
        return None
    windows = {tuple(right[index : index + size]) for index in range(len(right) - size + 1)}
    for index in range(len(left) - size + 1):
        candidate = tuple(left[index : index + size])
        if candidate in windows:
            return " ".join(candidate)
    return None


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
        generated_parts = [publication.title, publication.introduction]
        for index, update in enumerate(publication.updates):
            generated_parts.extend((update.heading, update.description, update.why_read))
            unknown = [item for item in update.evidence_ids if item not in evidence_by_id]
            if unknown:
                findings.append(
                    Finding(
                        category=FindingCategory.INTEGRITY,
                        affected_content=f"update {index + 1}",
                        correction=f"Replace unknown evidence references: {', '.join(unknown)}.",
                        rule="evidence-resolves",
                    )
                )
        generated = " ".join(generated_parts)
        for item in evidence:
            phrase = _overlap(generated, item.text)
            if phrase:
                findings.append(
                    Finding(
                        category=FindingCategory.ORIGINALITY,
                        affected_content=f"phrase: {phrase}",
                        correction="Rewrite in original, higher-level language.",
                        rule="originality",
                    )
                )
                break

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
        forbidden = sorted(set(parser.tags) & {"script", "iframe", "object", "embed", "form"})
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
