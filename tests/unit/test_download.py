from pathlib import Path

import pytest
from yt_dlp.utils import DownloadError

from claimlens.ingest import download
from claimlens.ingest.download import download_video, ensure_public_url


def fake_dns(monkeypatch: pytest.MonkeyPatch, ip: str) -> None:
    monkeypatch.setattr(
        download.socket, "getaddrinfo", lambda *_a, **_k: [(2, 1, 6, "", (ip, 443))]
    )


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "::1"])
def test_ensure_public_url_blocks_internal_addresses(monkeypatch: pytest.MonkeyPatch, ip: str):
    fake_dns(monkeypatch, ip)
    with pytest.raises(ValueError, match="public internet"):
        ensure_public_url("https://internal.example.com/video.mp4")


def test_ensure_public_url_allows_public_address(monkeypatch: pytest.MonkeyPatch):
    fake_dns(monkeypatch, "93.184.216.34")
    ensure_public_url("https://example.com/video.mp4")


def test_download_video_rejects_non_http_url(tmp_path: Path):
    with pytest.raises(ValueError, match="HTTP or HTTPS"):
        download_video("file:///tmp/reel.mp4", tmp_path / "reel.mp4")


def test_download_video_moves_file_to_destination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_dns(monkeypatch, "93.184.216.34")

    class FakeYoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_info(self, _url, download):
            assert download is True
            (tmp_path / "reel.webm").write_bytes(b"reel!")
            return {}

        def prepare_filename(self, _info):
            return str(tmp_path / "reel.webm")

    monkeypatch.setattr(download.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    destination = tmp_path / "reel.mp4"

    result = download_video("https://www.instagram.com/reel/abc/", destination)

    assert result == destination
    assert destination.read_bytes() == b"reel!"


def test_download_video_wraps_downloader_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_dns(monkeypatch, "93.184.216.34")

    class FailingYoutubeDL:
        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_info(self, _url, download):
            raise DownloadError("login required")

    monkeypatch.setattr(download.yt_dlp, "YoutubeDL", FailingYoutubeDL)

    with pytest.raises(ValueError, match="Could not download"):
        download_video("https://www.instagram.com/reel/abc/", tmp_path / "reel.mp4")
