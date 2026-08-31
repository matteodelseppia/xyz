from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .models import SourceRegistry

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


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
    producer_provider: str = "openrouter"
    producer_model: str = "anthropic/claude-sonnet-4"
    reviewer_provider: str = "openrouter"
    reviewer_model: str = "google/gemini-2.5-flash"
    max_iterations: int = Field(default=3, ge=1, le=8)
    malformed_retries: int = Field(default=2, ge=0, le=5)
    session_timeout_seconds: float = Field(default=300, ge=10, le=1800)
    tool_budget: int = Field(default=24, ge=1, le=100)
    source_registry_path: Path = REPOSITORY_ROOT / "config/sources.json"
    prompt_dir: Path = REPOSITORY_ROOT / "prompts"
    template_dir: Path = REPOSITORY_ROOT / "templates"
    pi_extension_path: Path = REPOSITORY_ROOT / "pi-extension/src/index.ts"
    source_revision: str = "unknown"
    retention_days: int = Field(default=7, ge=1, le=31)

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
        return self

    def registry(self) -> SourceRegistry:
        return SourceRegistry.model_validate_json(self.source_registry_path.read_text())

    def extension_environment(self, role: str) -> dict[str, str]:
        return {
            "XYZ_PI_ROLE": role,
            "XYZ_SOURCE_REGISTRY": json.dumps(self.registry().model_dump(mode="json")),
            "XYZ_TOOL_BUDGET": str(
                (self.tool_budget + 1) // 2 if role == "producer" else self.tool_budget // 2
            ),
        }
