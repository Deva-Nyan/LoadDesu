# utils.py
import re
import hashlib
import subprocess
import json
import logging
import asyncio
from typing import Optional, Tuple, Dict, Any
from urllib.parse import urlparse

from config import ALLOWED_HOSTS

_YT_ID = re.compile(r'(?:v=|/shorts/|youtu\.be/)([A-Za-z0-9_-]{11})')


def normalize_youtube_url(url: str) -> str:
    """Нормализует YouTube URL"""
    if "youtu" in url:
        m = _YT_ID.search(url)
        if m:
            return f"https://www.youtube.com/watch?v={m.group(1)}"
    return url


def extract_youtube_id(url: str) -> Optional[str]:
    """Извлекает YouTube ID из URL"""
    m = _YT_ID.search(url)
    return m.group(1) if m else None


def format_bytes(n: int) -> str:
    """Форматирует размер в байтах"""
    x = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if x < 1024 or unit == "GB":
            return f"{x:.0f} {unit}" if unit == "B" else f"{x:.2f} {unit}"
        x /= 1024


def _pick_single_path(stdout: str) -> str:
    """Выбирает путь из вывода yt-dlp"""
    lines = [ln.strip() for ln in stdout.splitlines() if ln.strip()]
    if not lines:
        raise RuntimeError("yt-dlp did not print any file path")
    if len(lines) > 1:
        logging.warning(f"[YTDLP] multiple outputs detected ({len(lines)}). Using the last one:\n" +
                        "\n".join(lines))
    return lines[-1]


def _origin(url: str) -> str:
    """Возвращает origin URL"""
    u = urlparse(url)
    return f"{u.scheme}://{u.netloc}/"


def _hostname(url: str) -> Optional[str]:
    """Извлекает hostname (без порта) в нижнем регистре."""
    host = urlparse(url).hostname
    return host.lower() if host else None


def is_url_allowed(url: str) -> bool:
    """Проверяет, входит ли URL в белый список доменов."""
    if not ALLOWED_HOSTS:
        return True

    host = _hostname(url)
    if not host:
        return False

    for allowed in ALLOWED_HOSTS:
        allowed = allowed.lstrip(".")
        if host == allowed or host.endswith("." + allowed):
            return True
    return False


async def run_io(func, *args, **kwargs):
    """Выполняет синхронную функцию в отдельном потоке"""
    return await asyncio.to_thread(func, *args, **kwargs)


def ytdlp_info(url: str) -> Dict[str, Any]:
    """Получает метаданные через yt-dlp -J"""
    r = subprocess.run(["yt-dlp", "-J", url], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def extract_title_artist(url: str, fallback_title: Optional[str] = None) -> Tuple[str, str]:
    """Возвращает (полный заголовок, артист) для красивой карточки"""
    try:
        info = ytdlp_info(url)
        title_full = info.get("track") or info.get("title") or fallback_title or "Audio"
        artist = info.get("artist") or info.get("uploader") or ""
        return title_full, artist
    except Exception:
        return fallback_title or "Audio", ""


def get_content_key_and_title(url: str):
    """Генерирует ключ для кеша и получает title"""
    url = normalize_youtube_url(url)
    try:
        r = subprocess.run(
            ["yt-dlp", "--ignore-config", "-J", "--no-playlist", url],
            capture_output=True, text=True, check=True
        )
        info = json.loads(r.stdout)
        extractor = (info.get("extractor_key") or info.get("extractor") or "unknown")
        vid = info.get("id")
        title = info.get("title") or "video"
        if extractor.lower() == "youtube" and not vid:
            vid = extract_youtube_id(url)
        if extractor.lower() == "youtube" and vid:
            return f"YouTube:{vid}", title
        if vid and extractor:
            return f"{extractor}:{vid}", title
    except subprocess.CalledProcessError as e:
        logging.warning(f"[CKEY] -J failed: {e}")

    yid = extract_youtube_id(url)
    if yid:
        return f"YouTube:{yid}", None

    return "urlsha1:" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:16], None


def detect_media_kind_and_key(url: str) -> Tuple[str, str, Optional[str]]:
    """
    Определяет тип медиа: 'video' | 'audio' | 'unknown'
    Возвращает (mode, content_key, title)
    """
    try:
        info = ytdlp_info(url)
        fmts = info.get("formats", []) or []
        has_video = any(f.get("vcodec") not in (None, "none") for f in fmts)
        has_audio_only = any((f.get("vcodec") in (None, "none")) and 
                            (f.get("acodec") not in (None, "none")) for f in fmts)
        extr = info.get("extractor") or info.get("extractor_key") or "unknown"
        vid  = info.get("id") or ""
        title = info.get("title")
        key = f"{extr}:{vid}" if vid else f"{extr}:{hash(url)}"
        mode = "video" if has_video else ("audio" if has_audio_only else "unknown")
        logging.info(f"[AUTO/DETECT] {mode} key={key}")
        return mode, key, title
    except Exception as e:
        logging.warning(f"[AUTO/DETECT] probe failed: {e}")
        key, title = get_content_key_and_title(url)
        return "unknown", key, title
