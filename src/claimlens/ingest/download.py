"""Download a video from any public URL (Instagram, YouTube, TikTok, direct links...)."""

import argparse
import ipaddress
import socket
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from yt_dlp.utils import DownloadError, match_filter_func

DEFAULT_MAX_BYTES = 100 * 1024 * 1024
DEFAULT_MAX_DURATION_S = 180


def ensure_public_url(url: str) -> None:
    """Reject URLs whose host is not a public internet address (SSRF protection).

    Without this, a user could make our server fetch internal addresses such as
    localhost, private networks or the cloud metadata service (169.254.169.254).
    """
    parsed_url = urlparse(url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
        raise ValueError("URL must be an absolute HTTP or HTTPS URL")

    try:
        addresses = socket.getaddrinfo(parsed_url.hostname, parsed_url.port or 443)
    except socket.gaierror as error:
        raise ValueError(f"Could not resolve host {parsed_url.hostname!r}") from error

    # A hostname can resolve to several addresses; every one must be public.
    for *_, sockaddr in addresses:
        if not ipaddress.ip_address(sockaddr[0]).is_global:
            raise ValueError("URL must point to a public internet address")


def download_video(
    url: str,
    destination: Path,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_duration_s: int = DEFAULT_MAX_DURATION_S,
) -> Path:
    """Download the video at url to destination using yt-dlp."""
    ensure_public_url(url)
    if max_bytes <= 0 or max_duration_s <= 0:
        raise ValueError("max_bytes and max_duration_s must be positive")

    destination.parent.mkdir(parents=True, exist_ok=True)
    # yt-dlp picks the file extension itself, so download to a name without one first.
    output_template = str(destination.with_suffix("")) + ".%(ext)s"

    options = {
        "outtmpl": output_template,
        # Prefer mp4 video+audio (merged with ffmpeg); fall back to a single file, then anything.
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "max_filesize": max_bytes,
        # "<=?" lets videos with unknown duration through; the size cap still applies.
        "match_filter": match_filter_func(f"duration <=? {max_duration_s}"),
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        "socket_timeout": 30,
    }

    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(url, download=True)
            saved_path = Path(downloader.prepare_filename(info))
    except DownloadError as error:
        raise ValueError(f"Could not download a video from that URL: {error}") from error

    if not saved_path.exists():
        raise ValueError(
            f"No video was downloaded (over the {max_bytes}-byte or {max_duration_s}s limit?)"
        )
    if saved_path != destination:
        saved_path.replace(destination)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description="Download a public video URL for ClaimLens.")
    parser.add_argument("url", help="Public HTTP(S) URL of a video or reel")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("reel.mp4"),
        help="Where to save the video (default: reel.mp4)",
    )
    args = parser.parse_args()

    try:
        saved_path = download_video(args.url, args.output)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f"Video saved to {saved_path}")


if __name__ == "__main__":
    main()
