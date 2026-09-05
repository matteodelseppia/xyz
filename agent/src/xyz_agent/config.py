from __future__ import annotations

import json
import os
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .models import SourceRegistry, StrictModel


class EditorialAnchor(StrictModel):
    """A manually curated example used to calibrate editorial selection."""

    url: str
    title: str
    description: str
    tags: list[str]


class _LovedOnesParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.anchors: list[EditorialAnchor] = []
        self._tags: list[str] = []
        self._url = ""
        self._title: list[str] = []
        self._description: list[str] = []
        self._in_article = False
        self._in_title = False
        self._in_description = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "li" and values.get("data-tags") is not None:
            self._tags = (values.get("data-tags") or "").split()
        elif tag == "article":
            self._in_article = True
        elif self._in_article and tag == "h1":
            self._in_title = True
        elif self._in_article and tag == "a" and self._in_title:
            self._url = values.get("href") or ""
        elif self._in_article and tag == "p":
            self._in_description = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "h1":
            self._in_title = False
        elif tag == "p":
            self._in_description = False
        elif tag == "article":
            self._in_article = False
            if self._url and self._title and self._description:
                self.anchors.append(
                    EditorialAnchor(
                        url=self._url,
                        title="".join(self._title).strip(),
                        description="".join(self._description).strip(),
                        tags=self._tags,
                    )
                )
            self._url = ""
            self._title = []
            self._description = []
            self._tags = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title.append(data)
        elif self._in_description:
            self._description.append(data)


def load_editorial_anchors(path: Path) -> list[EditorialAnchor]:
    """Load the site's hand-picked loved ones without duplicating their data."""
    parser = _LovedOnesParser()
    parser.feed(path.read_text())
    if not parser.anchors:
        raise ValueError(f"No editorial anchors found in {path}")
    return parser.anchors


def _repository_root() -> Path:
    """Locate versioned inputs in both a checkout and the container image."""
    configured = os.environ.get("XYZ_REPOSITORY_ROOT")
    if configured:
        return Path(configured)
    candidates = (Path.cwd(), Path(__file__).resolve().parents[3], Path("/app"))
    for candidate in candidates:
        if (candidate / "config/sources.json").is_file() and (
            candidate / "prompts/producer.md"
        ).is_file():
            return candidate
    return Path.cwd()


REPOSITORY_ROOT = _repository_root()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="XYZ_", env_file=".env", extra="ignore")

    environment: Literal["local", "production", "test"] = "local"
    runtime: Literal["pi", "fake"] = "pi"
    storage: Literal["local", "s3"] = "local"
    storage_root: Path = REPOSITORY_ROOT / "storage"
    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None
    s3_region: str = "auto"
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    producer_provider: str = "opencode-go"
    producer_model: str = "deepseek-v4-pro"
    producer_thinking: Literal["off", "minimal", "low", "medium", "high", "xhigh", "max"] = "medium"
    reviewer_provider: str = "opencode-go"
    reviewer_model: str = "gpt-5.6-luna"
    reviewer_thinking: Literal["off", "minimal", "low", "medium", "high", "xhigh", "max"] = "high"
    scheduled_run_cron: str = "0 6 * * *"
    max_iterations: int = Field(default=3, ge=1, le=50)
    malformed_retries: int = Field(default=2, ge=0, le=5)
    max_validation_retries: int = Field(default=2, ge=0, le=10)
    session_timeout_seconds: float = Field(default=600, ge=10, le=3000)
    tool_budget: int = Field(default=100, ge=1, le=100)
    source_registry_path: Path = REPOSITORY_ROOT / "config/sources.json"
    prompt_dir: Path = REPOSITORY_ROOT / "prompts"
    template_dir: Path = REPOSITORY_ROOT / "templates"
    pi_extension_path: Path = REPOSITORY_ROOT / "pi-extension/src/index.ts"
    loved_ones_path: Path = REPOSITORY_ROOT / "web/public/loved-ones/index.html"
    source_revision: str = "unknown"
    retention_days: int = Field(default=7, ge=1, le=31)

    @field_validator("scheduled_run_cron")
    @classmethod
    def validate_scheduled_run_cron(cls, value: str) -> str:
        if value != "0 6 * * *":
            raise ValueError("scheduled_run_cron must match the Railway schedule: 0 6 * * *")
        return value

    @model_validator(mode="after")
    def validate_combinations(self) -> Settings:
        if self.storage == "s3" and not all(
            (self.s3_bucket, self.s3_access_key_id, self.s3_secret_access_key)
        ):
            raise ValueError("S3 storage requires bucket and credentials")
        if self.environment == "production":
            if self.runtime != "pi":
                raise ValueError("production requires the Pi runtime")
            if self.source_revision == "unknown":
                raise ValueError("production requires XYZ_SOURCE_REVISION")
            if self.producer_model == self.reviewer_model:
                raise ValueError("production producer and reviewer models must differ")
            if "openrouter" in (
                self.producer_provider,
                self.reviewer_provider,
            ) and not os.environ.get("OPENROUTER_API_KEY"):
                raise ValueError("OpenRouter models require OPENROUTER_API_KEY")
            if "opencode-go" in (
                self.producer_provider,
                self.reviewer_provider,
            ) and not os.environ.get("OPENCODE_API_KEY"):
                raise ValueError("OpenCode Go models require OPENCODE_API_KEY")
        return self

    def registry(self) -> SourceRegistry:
        return SourceRegistry.model_validate_json(self.source_registry_path.read_text())

    def editorial_anchors(self) -> list[EditorialAnchor]:
        return load_editorial_anchors(self.loved_ones_path)

    def extension_environment(self, role: str) -> dict[str, str]:
        return {
            "XYZ_PI_ROLE": role,
            "XYZ_SOURCE_REGISTRY": json.dumps(self.registry().model_dump(mode="json")),
            "XYZ_TOOL_BUDGET": str(
                (self.tool_budget + 1) // 2 if role == "producer" else self.tool_budget // 2
            ),
        }
