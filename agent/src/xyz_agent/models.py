from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Source(StrictModel):
    id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")]
    name: Annotated[str, Field(min_length=1, max_length=100)]
    description: Annotated[str, Field(min_length=1, max_length=300)]
    feed_url: AnyHttpUrl


class SourceRegistry(StrictModel):
    version: Annotated[int, Field(ge=1)] = 1
    sources: Annotated[list[Source], Field(min_length=1, max_length=100)]

    @field_validator("sources")
    @classmethod
    def unique_ids(cls, value: list[Source]) -> list[Source]:
        if len({source.id for source in value}) != len(value):
            raise ValueError("source ids must be unique")
        return value


class Evidence(StrictModel):
    id: Annotated[str, Field(pattern=r"^ev_[a-f0-9]{16}$")]
    source_id: str
    url: AnyHttpUrl
    title: Annotated[str, Field(max_length=300)] = ""
    published_at: datetime | None = None
    text: Annotated[str, Field(min_length=1, max_length=20_000)]
    kind: Literal["feed", "entry", "link"]


class Update(StrictModel):
    heading: Annotated[str, Field(min_length=1, max_length=100)]
    description: Annotated[str, Field(min_length=1, max_length=360)]
    why_read: Annotated[str, Field(min_length=1, max_length=220)]
    evidence_ids: Annotated[list[str], Field(min_length=1, max_length=5)]


class Publication(StrictModel):
    schema_version: Literal[1] = 1
    candidate_id: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{6,80}$")]
    publication_date: date
    title: Annotated[str, Field(min_length=1, max_length=100)]
    introduction: Annotated[str, Field(min_length=1, max_length=420)]
    updates: Annotated[list[Update], Field(max_length=12)] = Field(default_factory=list)
    revision_notes: Annotated[str | None, Field(max_length=500)] = None


class FindingCategory(StrEnum):
    FACTUAL_SUPPORT = "factual_support"
    ATTRIBUTION = "attribution"
    ORIGINALITY = "originality"
    RELEVANCE = "relevance"
    DUPLICATION = "duplication"
    READABILITY = "readability"
    BREVITY = "brevity"
    SECURITY = "security"
    INTEGRITY = "integrity"


class Finding(StrictModel):
    category: FindingCategory
    affected_content: Annotated[str, Field(min_length=1, max_length=160)]
    correction: Annotated[str, Field(min_length=1, max_length=360)]
    rule: Annotated[str | None, Field(max_length=100)] = None


class Review(StrictModel):
    schema_version: Literal[1] = 1
    candidate_id: str
    approved: bool
    findings: Annotated[list[Finding], Field(max_length=12)] = Field(default_factory=list)
    rationale: Annotated[str, Field(min_length=1, max_length=500)]

    @field_validator("findings")
    @classmethod
    def findings_match_verdict(cls, value: list[Finding], info: Any) -> list[Finding]:
        if info.data.get("approved") and value:
            raise ValueError("approved reviews cannot contain findings")
        return value


class Artifact(StrictModel):
    path: str
    sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    size: Annotated[int, Field(ge=0)]
    content_type: str


class Manifest(StrictModel):
    schema_version: Literal[1] = 1
    run_id: str
    publication_date: date
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    review_status: Literal["approved", "iteration_limit"]
    artifacts: list[Artifact]
    provenance: dict[str, str]
    validation_rules: list[str]


class DayPointer(StrictModel):
    schema_version: Literal[1] = 1
    date: date
    run_prefix: str
    manifest_sha256: str


class CurrentPointer(DayPointer):
    pass


class Calendar(StrictModel):
    schema_version: Literal[1] = 1
    dates: list[date]


class RenderedSet(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    files: dict[str, tuple[bytes, str]]
    manifest: Manifest


class RunResult(StrictModel):
    run_id: str
    publication_date: date
    status: Literal["published", "failed", "contended"]
    run_prefix: str | None = None
    review_status: str | None = None
    warnings: list[str] = Field(default_factory=list)
