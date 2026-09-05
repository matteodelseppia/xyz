from __future__ import annotations

import hashlib
import os
from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, cast

from botocore.exceptions import ClientError


class StorageError(RuntimeError):
    pass


class NotFound(StorageError):
    pass


class PreconditionFailed(StorageError):
    pass


@dataclass(frozen=True)
class StoredObject:
    data: bytes
    content_type: str
    etag: str


def safe_key(key: str) -> str:
    path = PurePosixPath(key)
    if not key or key.startswith("/") or ".." in path.parts or "\\" in key:
        raise ValueError(f"unsafe storage key: {key!r}")
    normalized = str(path)
    if normalized in ("", "."):
        raise ValueError("empty storage key")
    return normalized


class Storage(ABC):
    @abstractmethod
    def get(self, key: str) -> StoredObject: ...

    @abstractmethod
    def put(
        self,
        key: str,
        data: bytes,
        content_type: str,
        *,
        if_match: str | None = None,
        if_none_match: bool = False,
    ) -> str: ...

    @abstractmethod
    def delete(self, key: str, *, if_match: str | None = None) -> None: ...

    @abstractmethod
    def list(self, prefix: str) -> list[str]: ...


class LocalStorage(Storage):
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / safe_key(key)).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("storage path escaped root")
        return path

    @staticmethod
    def _etag(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    @contextmanager
    def _locked(self, path: Path) -> Iterator[None]:
        import fcntl

        path.parent.mkdir(parents=True, exist_ok=True)
        lock_dir = self.root / ".locks"
        lock_dir.mkdir(exist_ok=True)
        lock_name = hashlib.sha256(str(path.relative_to(self.root)).encode()).hexdigest()
        with (lock_dir / lock_name).open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def get(self, key: str) -> StoredObject:
        path = self._path(key)
        try:
            data = path.read_bytes()
        except FileNotFoundError as exc:
            raise NotFound(key) from exc
        content_type = "application/octet-stream"
        metadata = path.with_suffix(path.suffix + ".metadata")
        if metadata.exists():
            content_type = metadata.read_text().strip()
        return StoredObject(data, content_type, self._etag(data))

    def put(
        self,
        key: str,
        data: bytes,
        content_type: str,
        *,
        if_match: str | None = None,
        if_none_match: bool = False,
    ) -> str:
        path = self._path(key)
        with self._locked(path):
            if path.exists():
                current = self._etag(path.read_bytes())
                if if_none_match or (if_match is not None and current != if_match):
                    raise PreconditionFailed(key)
            elif if_match is not None:
                raise PreconditionFailed(key)
            temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            temporary.write_bytes(data)
            os.replace(temporary, path)
            metadata = path.with_suffix(path.suffix + ".metadata")
            metadata.write_text(content_type)
            return self._etag(data)

    def delete(self, key: str, *, if_match: str | None = None) -> None:
        path = self._path(key)
        with self._locked(path):
            if not path.exists():
                return
            if if_match is not None and self._etag(path.read_bytes()) != if_match:
                raise PreconditionFailed(key)
            path.unlink()
            path.with_suffix(path.suffix + ".metadata").unlink(missing_ok=True)
        for parent in path.parents:
            if parent == self.root:
                break
            try:
                parent.rmdir()
            except OSError:
                break

    def list(self, prefix: str) -> list[str]:
        normalized = safe_key(prefix.rstrip("/") or prefix)
        base = self._path(normalized)
        if base.is_file():
            return [normalized]
        if not base.exists():
            return []
        return sorted(
            str(path.relative_to(self.root))
            for path in base.rglob("*")
            if path.is_file()
            and not path.name.endswith(".metadata")
            and not path.name.endswith(".lock")
        )


class S3Storage(Storage):
    def __init__(
        self,
        bucket: str,
        *,
        endpoint_url: str | None,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        client: Any = None,
    ) -> None:
        if client is None:
            import boto3

            client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                region_name=region,
                aws_access_key_id=access_key_id,
                aws_secret_access_key=secret_access_key,
            )
        self.client = client
        self.bucket = bucket

    def get(self, key: str) -> StoredObject:
        key = safe_key(key)
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in ("NoSuchKey", "404", "NotFound"):
                raise NotFound(key) from exc
            raise StorageError(str(exc)) from exc
        data = response["Body"].read()
        return StoredObject(
            data=data,
            content_type=response.get("ContentType", "application/octet-stream"),
            etag=response["ETag"].strip('"'),
        )

    def put(
        self,
        key: str,
        data: bytes,
        content_type: str,
        *,
        if_match: str | None = None,
        if_none_match: bool = False,
    ) -> str:
        kwargs: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": safe_key(key),
            "Body": data,
            "ContentType": content_type,
        }
        if if_match is not None:
            kwargs["IfMatch"] = if_match
        if if_none_match:
            kwargs["IfNoneMatch"] = "*"
        try:
            response = self.client.put_object(**kwargs)
        except ClientError as exc:
            if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 412:
                raise PreconditionFailed(key) from exc
            raise StorageError(str(exc)) from exc
        etag = response.get("ETag", hashlib.sha256(data).hexdigest())
        return cast(str, etag).strip('"')

    def delete(self, key: str, *, if_match: str | None = None) -> None:
        kwargs: dict[str, Any] = {"Bucket": self.bucket, "Key": safe_key(key)}
        if if_match is not None:
            kwargs["IfMatch"] = if_match
        self.client.delete_object(**kwargs)

    def list(self, prefix: str) -> list[str]:
        paginator = self.client.get_paginator("list_objects_v2")
        return sorted(
            item["Key"]
            for page in paginator.paginate(Bucket=self.bucket, Prefix=safe_key(prefix))
            for item in page.get("Contents", [])
        )
