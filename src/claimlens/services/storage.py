"""Storage behind one interface: local disk now, GCS / S3 later without changing callers."""

import hashlib
import shutil
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

from claimlens.config.settings import get_settings


class Storage(Protocol):
    """What the rest of the code may rely on; any class with these methods qualifies."""

    def put_file(self, local_path: Path, key: str) -> None: ...

    def get_file(self, key: str, destination: Path) -> Path: ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None: ...


def video_key(url: str) -> str:
    """Deterministic key for a URL, so the same reel is stored (and processed) once."""
    parts = urlsplit(url.strip())
    # Drop the fragment and lowercase scheme/host; the query is kept since it can select the video.
    normalized = urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, "")
    )
    return f"videos/{hashlib.sha256(normalized.encode()).hexdigest()}.mp4"


class LocalStorage:
    """Stores objects as files under a root directory."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        # Keys like "../../etc/passwd" must not escape the storage root.
        if not path.is_relative_to(self.root):
            raise ValueError(f"Invalid storage key: {key!r}")
        return path

    def put_file(self, local_path: Path, key: str) -> None:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Copy to a temp name then rename, so readers never see a half-written file.
        partial = target.with_name(f"{target.name}.part")
        shutil.copyfile(local_path, partial)
        partial.replace(target)

    def get_file(self, key: str, destination: Path) -> Path:
        source = self._path(key)
        if not source.is_file():
            raise FileNotFoundError(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        return destination

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class GCSStorage:
    """Stores objects in a Google Cloud Storage bucket."""

    def __init__(self, bucket_name: str, client: Any = None) -> None:
        # Imported here so local development works without cloud credentials.
        from google.cloud import storage

        self._bucket = (client or storage.Client()).bucket(bucket_name)

    def put_file(self, local_path: Path, key: str) -> None:
        # GCS uploads are atomic: the object appears only once the upload completes.
        self._bucket.blob(key).upload_from_filename(str(local_path))

    def get_file(self, key: str, destination: Path) -> Path:
        from google.api_core.exceptions import NotFound

        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._bucket.blob(key).download_to_filename(str(destination))
        except NotFound as error:
            destination.unlink(missing_ok=True)
            raise FileNotFoundError(key) from error
        return destination

    def exists(self, key: str) -> bool:
        return self._bucket.blob(key).exists()

    def delete(self, key: str) -> None:
        from google.api_core.exceptions import NotFound

        try:
            self._bucket.blob(key).delete()
        except NotFound:
            pass


def get_storage() -> Storage:
    """Pick the backend from settings: GCS if a bucket is configured, else local disk."""
    settings = get_settings()
    if settings.gcs_bucket:
        return GCSStorage(settings.gcs_bucket)
    return LocalStorage(Path(settings.local_storage_dir))
