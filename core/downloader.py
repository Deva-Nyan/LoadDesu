"""Interface to yt-dlp/ffprobe with a clean asyncio-friendly surface."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Tuple

from config import (
    COOKIES_FILE,
    COOKIES_FROM_BROWSER,
    DEFAULT_UA,
    SAVE_DIR,
    SMART_FMT_1080,
)
from core.formats import FormatOption, FormatSummary
from core.utils import format_bytes


class DownloadError(RuntimeError):
    """Raised when yt-dlp or ffmpeg fail to provide the expected output."""


# What: Execute yt-dlp and return the downloaded file path.
# Inputs: ``url`` - media link; ``preferred_format`` - yt-dlp format selector string.
# Outputs: ``Path`` pointing to the downloaded file on success, raises DownloadError.
def download_video(url: str, preferred_format: str = SMART_FMT_1080) -> Path:
    output_template = Path(SAVE_DIR) / "%(title)s [%(id)s].%(ext)s"
    command = [
        "yt-dlp",
        "-f",
        preferred_format,
        "--merge-output-format",
        "mp4",
        "--no-playlist",
        "--no-simulate",
        "--print",
        "after_move:filepath",
        "-o",
        str(output_template),
        url,
    ]
    if COOKIES_FILE:
        command.extend(["--cookies", COOKIES_FILE])
    elif COOKIES_FROM_BROWSER:
        command.extend(["--cookies-from-browser", COOKIES_FROM_BROWSER])
    command.extend(["--add-header", f"User-Agent: {DEFAULT_UA}"])

    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:  # pragma: no cover - CLI error path
        stderr = exc.stderr or exc.stdout or "yt-dlp failed"
        raise DownloadError(stderr.strip()) from exc

    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise DownloadError("yt-dlp did not report an output file path")
    output_path = Path(lines[-1])
    if not output_path.exists():
        raise DownloadError("yt-dlp reported a path that does not exist")
    return output_path


# What: Download media using an explicit yt-dlp format selector string.
# Inputs: ``url`` - media link; ``format_selector`` - raw ``-f`` argument.
# Outputs: ``Path`` to the downloaded file.
def download_with_format(url: str, format_selector: str) -> Path:
    return download_video(url, preferred_format=format_selector)


# What: Extract duration/size information for Telegram upload hints.
# Inputs: ``video_path`` - path to the downloaded file.
# Outputs: Tuple ``(duration_seconds, width_pixels, height_pixels)``.
def probe_video_details(video_path: Path) -> Tuple[int, int, int]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,duration",
        "-of",
        "json",
        str(video_path),
    ]
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout)
        stream = (payload.get("streams") or [{}])[0]
        duration = int(float(stream.get("duration", 0)))
        width = int(stream.get("width", 0) or 0)
        height = int(stream.get("height", 0) or 0)
        return duration, width, height
    except Exception as exc:  # pragma: no cover - best effort fallback path
        logging.debug("ffprobe metadata extraction failed: %s", exc)
        return 0, 0, 0


# What: Inspect the remote resource and return human friendly format choices.
# Inputs: ``url`` - media link supplied by the user; ``limit`` - max entries per
#     category.
# Outputs: ``FormatSummary`` describing available video/audio variants.
def collect_format_summary(url: str, limit: int = 6) -> FormatSummary:
    command = [
        "yt-dlp",
        "--ignore-config",
        "-J",
        "--no-playlist",
        url,
    ]
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:  # pragma: no cover - CLI error path
        stderr = exc.stderr or exc.stdout or "yt-dlp metadata probe failed"
        raise DownloadError(stderr.strip()) from exc

    payload = json.loads(result.stdout)
    formats = payload.get("formats") or []
    title = payload.get("title") or "video"

    summary = FormatSummary(title=title)
    for entry in formats:
        format_id = str(entry.get("format_id"))
        if not format_id:
            continue

        vcodec = entry.get("vcodec")
        acodec = entry.get("acodec")
        height = entry.get("height")
        ext = entry.get("ext") or ""
        fps = entry.get("fps")
        bitrate = entry.get("tbr") or entry.get("abr")
        size = entry.get("filesize") or entry.get("filesize_approx")

        label_parts = []
        if height:
            label_parts.append(f"{int(height)}p")
        if ext:
            label_parts.append(ext)
        if fps:
            label_parts.append(f"{int(fps)}fps")
        if bitrate:
            label_parts.append(f"~{int(bitrate)}kbps")
        label = " ".join(label_parts) or entry.get("format_note") or format_id
        if size:
            try:
                label += f" ({format_bytes(int(size))})"
            except (TypeError, ValueError):  # pragma: no cover - best effort
                pass

        option = FormatOption(format_id=format_id, label=label)

        if vcodec not in (None, "none") and acodec not in (None, "none"):
            summary.progressive.append(option)
        elif vcodec not in (None, "none"):
            summary.video_only.append(option)
        elif acodec not in (None, "none"):
            summary.audio_only.append(option)

    def _sort_height(option: FormatOption) -> int:
        for part in option.label.split():
            if part.endswith("p") and part[:-1].isdigit():
                return int(part[:-1])
        return 0

    def _sort_bitrate(option: FormatOption) -> int:
        for part in option.label.split():
            if part.startswith("~") and part.endswith("kbps"):
                number = part[1:-4]
                if number.isdigit():
                    return int(number)
        return 0

    summary.progressive.sort(key=_sort_height, reverse=True)
    summary.video_only.sort(key=_sort_height, reverse=True)
    summary.audio_only.sort(key=_sort_bitrate, reverse=True)

    summary.progressive = summary.progressive[:limit]
    summary.video_only = summary.video_only[:limit]
    summary.audio_only = summary.audio_only[:limit]
    return summary


# What: Remove a file without raising errors to the caller.
# Inputs: ``path`` - filesystem location to delete.
# Outputs: ``None``; silently ignores missing files and IO errors.
def delete_file_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)  # type: ignore[arg-type]
    except AttributeError:  # pragma: no cover - Python < 3.8 fallback
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            logging.debug("Failed to delete temporary file %s: %s", path, exc)
    except OSError as exc:  # pragma: no cover - unexpected errors
        logging.debug("Failed to delete temporary file %s: %s", path, exc)
