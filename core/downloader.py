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
