"""Configuration values shared across the application."""

from __future__ import annotations

import os
from asyncio import Semaphore
from pathlib import Path
from typing import Optional


# What: Convert an environment variable to ``int`` safely.
# Inputs: ``name`` - variable name; ``default`` - fallback when missing/invalid.
# Outputs: Parsed integer value.
def _int_env(name: str, default: int = 0) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


TOKEN = os.getenv("BOT_TOKEN", "")
SAVE_DIR = os.getenv("SAVE_DIR", "/opt/mybot/video")
DB_PATH = os.path.join(SAVE_DIR, "cache.db")
PLACEHOLDER_PHOTO_ID = os.getenv("PLACEHOLDER_ID", "")
MAX_TG_SIZE = 50 * 1024 * 1024
OWNER_ID = _int_env("OWNER_ID")

PYRO_API_ID = _int_env("PYRO_API_ID")
PYRO_API_HASH = os.getenv("PYRO_API_HASH", "")
PYRO_SESSION = os.getenv("PYRO_SESSION", "userbot_session")
CACHE_CHAT_ID = _int_env("CACHE_CHAT_ID")
CACHE_THREAD_ID = _int_env("CACHE_THREAD_ID")
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

COOKIES_FILE = os.getenv("COOKIES_FILE", "")
COOKIES_FROM_BROWSER = os.getenv("COOKIES_FROM_BROWSER", "")
if not COOKIES_FILE and os.path.exists("/opt/mybot/video/cookies.txt"):
    COOKIES_FILE = "/opt/mybot/video/cookies.txt"

SMART_FMT = "bv*[height<=1080]+ba/b[height<=1080]/b"
SMART_FMT_1080 = SMART_FMT
GIF_FMT = "bv*[height<=480]+ba/b[height<=480]/b"

DL_SEM = Semaphore(int(os.getenv("MAX_PARALLEL", "2")))

Path(SAVE_DIR).mkdir(parents=True, exist_ok=True)
