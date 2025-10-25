import re
from urllib.parse import urlparse


def format_bytes(n: int) -> str:
    x = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if x < 1024 or unit == "GB":
            return f"{x:.0f} {unit}" if unit == "B" else f"{x:.2f} {unit}"
    x /= 1024


_YT_ID = re.compile(r'(?:v=|/shorts/|youtu\.be/)([A-Za-z0-9_-]{11})')


def normalize_youtube_url(url: str) -> str:
    if "youtu" in url:
        m = _YT_ID.search(url)
        if m:
            return f"https://www.youtube.com/watch?v={m.group(1)}"
    return url


def extract_youtube_id(url: str):
    m = _YT_ID.search(url)
    return m.group(1) if m else None


def origin(url: str) -> str:
    u = urlparse(url)
    return f"{u.scheme}://{u.netloc}/"