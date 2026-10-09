from pathlib import Path
from types import SimpleNamespace

import pytest
from google.api_core.exceptions import NotFound

from claimlens.services import storage as storage_module
from claimlens.services.storage import GCSStorage, LocalStorage, Storage, video_key


def test_video_key_is_stable_and_ignores_fragment_and_host_case():
    a = video_key("https://WWW.Instagram.com/reel/abc/#top")
    b = video_key("https://www.instagram.com/reel/abc/")
    assert a == b
    assert a.startswith("videos/") and a.endswith(".mp4")


def test_video_key_differs_for_different_urls():
    assert video_key("https://example.com/a") != video_key("https://example.com/b")


def test_local_storage_round_trip(tmp_path: Path):
    storage: Storage = LocalStorage(tmp_path / "store")
    source = tmp_path / "reel.mp4"
    source.write_bytes(b"data")

    assert not storage.exists("videos/x.mp4")
    storage.put_file(source, "videos/x.mp4")
    assert storage.exists("videos/x.mp4")

    fetched = storage.get_file("videos/x.mp4", tmp_path / "out" / "copy.mp4")
    assert fetched.read_bytes() == b"data"

    storage.delete("videos/x.mp4")
    assert not storage.exists("videos/x.mp4")
    storage.delete("videos/x.mp4")  # deleting twice is safe


def test_local_storage_rejects_path_traversal(tmp_path: Path):
    storage = LocalStorage(tmp_path / "store")
    with pytest.raises(ValueError, match="Invalid storage key"):
        storage.exists("../outside.mp4")


def test_local_storage_missing_key_raises(tmp_path: Path):
    storage = LocalStorage(tmp_path / "store")
    with pytest.raises(FileNotFoundError):
        storage.get_file("videos/none.mp4", tmp_path / "x.mp4")


class FakeBlob:
    def __init__(self, objects: dict[str, bytes], key: str) -> None:
        self.objects, self.key = objects, key

    def upload_from_filename(self, path: str) -> None:
        self.objects[self.key] = Path(path).read_bytes()

    def download_to_filename(self, path: str) -> None:
        if self.key not in self.objects:
            raise NotFound("missing")
        Path(path).write_bytes(self.objects[self.key])

    def exists(self) -> bool:
        return self.key in self.objects

    def delete(self) -> None:
        if self.key not in self.objects:
            raise NotFound("missing")
        del self.objects[self.key]


class FakeClient:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def bucket(self, _name: str):
        return SimpleNamespace(blob=lambda key: FakeBlob(self.objects, key))


def test_gcs_storage_round_trip(tmp_path: Path):
    client = FakeClient()
    storage: Storage = GCSStorage("my-bucket", client=client)
    source = tmp_path / "reel.mp4"
    source.write_bytes(b"data")

    assert not storage.exists("videos/x.mp4")
    storage.put_file(source, "videos/x.mp4")
    assert storage.exists("videos/x.mp4")
    assert storage.get_file("videos/x.mp4", tmp_path / "out" / "c.mp4").read_bytes() == b"data"

    storage.delete("videos/x.mp4")
    assert not storage.exists("videos/x.mp4")
    storage.delete("videos/x.mp4")  # deleting twice is safe


def test_gcs_storage_missing_key_raises_file_not_found(tmp_path: Path):
    storage = GCSStorage("my-bucket", client=FakeClient())
    with pytest.raises(FileNotFoundError):
        storage.get_file("videos/none.mp4", tmp_path / "x.mp4")
    assert not (tmp_path / "x.mp4").exists()


def test_get_storage_picks_backend_from_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(
        storage_module,
        "get_settings",
        lambda: SimpleNamespace(gcs_bucket=None, local_storage_dir=str(tmp_path)),
    )
    assert isinstance(storage_module.get_storage(), LocalStorage)

    monkeypatch.setattr(storage_module, "GCSStorage", lambda bucket: ("gcs", bucket))
    monkeypatch.setattr(
        storage_module,
        "get_settings",
        lambda: SimpleNamespace(gcs_bucket="b", local_storage_dir=str(tmp_path)),
    )
    assert storage_module.get_storage() == ("gcs", "b")
