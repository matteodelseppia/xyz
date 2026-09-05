from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from pydantic import BaseModel

from .models import Calendar, CurrentPointer, DayPointer, RenderedSet
from .storage import NotFound, PreconditionFailed, Storage


class LockContended(RuntimeError):
    pass


@dataclass(frozen=True)
class PublicationReceipt:
    run_prefix: str
    warnings: list[str]


class Publisher:
    def __init__(self, storage: Storage, retention_days: int = 7) -> None:
        self.storage = storage
        self.retention_days = retention_days

    def _put_json(self, key: str, document: object) -> str:
        if isinstance(document, BaseModel):
            data = document.model_dump_json().encode()
        else:
            data = json.dumps(document, separators=(",", ":"), sort_keys=True).encode()
        try:
            current = self.storage.get(key)
            return self.storage.put(key, data, "application/json", if_match=current.etag)
        except NotFound:
            return self.storage.put(key, data, "application/json", if_none_match=True)

    def acquire_lock(self, publication_date: date, owner: str, lease_seconds: int = 900) -> str:
        key = f"locks/{publication_date}.json"
        now = datetime.now(UTC)
        document = {
            "owner": owner,
            "expires_at": (now + timedelta(seconds=lease_seconds)).isoformat(),
        }
        data = json.dumps(document, separators=(",", ":")).encode()
        try:
            self.storage.put(key, data, "application/json", if_none_match=True)
            return key
        except PreconditionFailed:
            existing = self.storage.get(key)
            lock = json.loads(existing.data)
            if datetime.fromisoformat(lock["expires_at"]) > now:
                raise LockContended(publication_date.isoformat()) from None
            try:
                self.storage.put(key, data, "application/json", if_match=existing.etag)
            except PreconditionFailed as exc:
                raise LockContended(publication_date.isoformat()) from exc
            return key

    def release_lock(self, lock_key: str, owner: str) -> None:
        try:
            existing = self.storage.get(lock_key)
        except NotFound:
            return
        if json.loads(existing.data).get("owner") == owner:
            self.storage.delete(lock_key, if_match=existing.etag)

    def retained_dates(self) -> list[date]:
        try:
            calendar = Calendar.model_validate_json(self.storage.get("calendar.json").data)
        except NotFound:
            return []
        return calendar.dates

    def publish(self, rendered: RenderedSet) -> PublicationReceipt:
        manifest = rendered.manifest
        run_prefix = f"runs/{manifest.publication_date}/{manifest.run_id}"
        for relative, (data, content_type) in rendered.files.items():
            self.storage.put(f"{run_prefix}/{relative}", data, content_type, if_none_match=True)
        for artifact in manifest.artifacts:
            stored = self.storage.get(f"{run_prefix}/{artifact.path}")
            if hashlib.sha256(stored.data).hexdigest() != artifact.sha256:
                raise RuntimeError(f"artifact hash verification failed: {artifact.path}")
        manifest_data = rendered.files["manifest.json"][0]
        pointer = DayPointer(
            date=manifest.publication_date,
            run_prefix=run_prefix,
            manifest_sha256=hashlib.sha256(manifest_data).hexdigest(),
        )
        self._put_json(f"days/{manifest.publication_date}.json", pointer)

        dates = sorted(set(self.retained_dates() + [manifest.publication_date]), reverse=True)
        kept = dates[: self.retention_days]
        latest = kept[0]
        latest_pointer = DayPointer.model_validate_json(
            self.storage.get(f"days/{latest}.json").data
        )
        self._put_json("current.json", CurrentPointer(**latest_pointer.model_dump()))
        self._put_json("calendar.json", Calendar(dates=kept))

        warnings: list[str] = []
        for expired in dates[self.retention_days :]:
            try:
                old_pointer = DayPointer.model_validate_json(
                    self.storage.get(f"days/{expired}.json").data
                )
                self.storage.delete(f"days/{expired}.json")
                for key in self.storage.list(old_pointer.run_prefix):
                    self.storage.delete(key)
            except Exception as exc:  # cleanup is best-effort after pointer publication
                warnings.append(f"cleanup failed for {expired}: {type(exc).__name__}")
        return PublicationReceipt(run_prefix=run_prefix, warnings=warnings)
