from pathlib import Path

import pytest
from xyz_agent.storage import LocalStorage, NotFound, PreconditionFailed


def test_local_storage_contract(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    etag = storage.put("a/b.txt", b"one", "text/plain", if_none_match=True)
    assert storage.get("a/b.txt").data == b"one"
    assert storage.list("a") == ["a/b.txt"]
    with pytest.raises(PreconditionFailed):
        storage.put("a/b.txt", b"two", "text/plain", if_none_match=True)
    storage.put("a/b.txt", b"two", "text/plain", if_match=etag)
    storage.delete("a/b.txt")
    with pytest.raises(NotFound):
        storage.get("a/b.txt")


@pytest.mark.parametrize("key", ["../secret", "/absolute", "a/../../b", "a\\b"])
def test_local_storage_rejects_traversal(tmp_path: Path, key: str) -> None:
    with pytest.raises(ValueError):
        LocalStorage(tmp_path).put(key, b"x", "text/plain")
