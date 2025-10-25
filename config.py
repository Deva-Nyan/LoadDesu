#config.py
import os
from pathlib import Path
from asyncio import Semaphore


# === Конфигурация / константы ===
TOKEN = os.getenv("BOT_TOKEN", "")
SAVE_DIR = os.getenv("SAVE_DIR", "/opt/mybot/video")
DB_PATH = os.path.join(SAVE_DIR, "cache.db")
PLACEHOLDER_PHOTO_ID = os.getenv("PLACEHOLDER_ID", "")
MAX_TG_SIZE = 50 * 1024 * 1024
OWNER_ID = int(os.getenv("OWNER_ID", ""))


PYRO_API_ID = int(os.getenv("PYRO_API_ID", ""))
PYRO_API_HASH = os.getenv("PYRO_API_HASH", "")
PYRO_SESSION = os.getenv("PYRO_SESSION", "userbot_session")
CACHE_CHAT_ID = int(os.getenv("CACHE_CHAT_ID", ""))
CACHE_THREAD_ID = int(os.getenv("CACHE_THREAD_ID", ""))
DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


COOKIES_FILE = os.getenv("COOKIES_FILE", "/opt/mybot/yt_cookies.txt")
COOKIES_FROM_BROWSER = os.getenv("COOKIES_FROM_BROWSER", "")
if not COOKIES_FILE and os.path.exists("/opt/mybot/video/cookies.txt"):
    COOKIES_FILE = "/opt/mybot/video/cookies.txt"


SMART_FMT = "bv*[height<=1080]+ba/b[height<=1080]/b"
SMART_FMT_1080 = SMART_FMT
GIF_FMT = "bv*[height<=480]+ba/b[height<=480]/b"


# Лимит параллельных тяжёлых задач
DL_SEM = Semaphore(int(os.getenv("MAX_PARALLEL", "2")))


Path(SAVE_DIR).mkdir(parents=True, exist_ok=True)
