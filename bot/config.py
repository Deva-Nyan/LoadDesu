# config.py
import os
from pathlib import Path

# === Основные настройки (все токены — только из ENV) ===
TOKEN = os.getenv("BOT_TOKEN", "")                   # Telegram bot token (из окружения)
OWNER_ID = int(os.getenv("OWNER_ID", "0"))           # Твой telegram ID (из окружения)

# === Директории ===
SAVE_DIR = os.getenv("SAVE_DIR", "/opt/mybot/video")
DB_PATH = os.path.join(SAVE_DIR, "cache.db")
os.makedirs(SAVE_DIR, exist_ok=True)

# === Telegram ===
PLACEHOLDER_PHOTO_ID = os.getenv("PLACEHOLDER_PHOTO_ID", "")
MAX_TG_SIZE = 50 * 1024 * 1024  # 50 MB

# === Pyrogram (Userbot) — всё только из ENV ===
PYRO_API_ID = int(os.getenv("PYRO_API_ID", "0"))
PYRO_API_HASH = os.getenv("PYRO_API_HASH", "")
PYRO_SESSION = os.getenv("PYRO_SESSION", "userbot_session")
CACHE_CHAT_ID = int(os.getenv("CACHE_CHAT_ID", "0"))
CACHE_THREAD_ID = int(os.getenv("CACHE_THREAD_ID", "0"))

# === Bot info (заполняется при старте) ===
BOT_USERNAME = None
BOT_ID = None

# === yt-dlp настройки ===
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)

COOKIES_FILE = os.getenv("COOKIES_FILE", "")
COOKIES_FROM_BROWSER = os.getenv("COOKIES_FROM_BROWSER", "")

# === Форматы ===
SMART_FMT = "bestvideo[height<=1080]+bestaudio/best[height<=1080]"
SMART_FMT_1080 = SMART_FMT
GIF_FMT = "bv*[height<=480]+ba/b[height<=480]/b"

# === Параллельность ===
MAX_PARALLEL = int(os.getenv("MAX_PARALLEL", "2"))
