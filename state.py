# state.py
from typing import Optional, Dict, Tuple
from pyrogram import Client as PyroClient
import asyncio
from config import PYRO_API_ID, PYRO_API_HASH, PYRO_SESSION

pyro_app: Optional[PyroClient] = None
BOT_USERNAME: Optional[str] = None
BOT_ID: Optional[int] = None
USERBOT_ID: Optional[int] = None

_pyro_lock = asyncio.Lock()

async def get_pyro_app() -> PyroClient:
    global pyro_app, USERBOT_ID
    if pyro_app and pyro_app.is_connected:
        return pyro_app
    async with _pyro_lock:
        if pyro_app and pyro_app.is_connected:
            return pyro_app
        if not PYRO_API_ID or not PYRO_API_HASH:
            raise RuntimeError("PYRO_API_ID / PYRO_API_HASH не заданы (env).")

        app = PyroClient(PYRO_SESSION, api_id=PYRO_API_ID, api_hash=PYRO_API_HASH)
        await app.start()
        me = await app.get_me()
        USERBOT_ID = me.id          # <-- запомнили id аккаунта-юзербота
        print(f"[PYRO] soft-start -> @{getattr(me, 'username', None)} (id={me.id})")
        pyro_app = app
        return pyro_app

async def set_bot_identity(username: Optional[str], bot_id: Optional[int]) -> None:
    """Сохраняет username/id твоего PTB-бота, чтобы userbot писал ему в DM."""
    global BOT_USERNAME, BOT_ID
    BOT_USERNAME = username
    BOT_ID = bot_id

INFLIGHT: Dict[Tuple[str, str], asyncio.Lock] = {}

def get_inflight_lock(content_key: str, variant: str) -> asyncio.Lock:
    k = (content_key, variant)
    lk = INFLIGHT.get(k)
    if not lk:
        lk = asyncio.Lock()
        INFLIGHT[k] = lk
    return lk

async def close_pyro_app() -> None:
    global pyro_app
    if pyro_app and pyro_app.is_connected:
        await pyro_app.stop()
        pyro_app = None

# runtime словари
DOWNLOAD_TASKS: Dict[str, str] = {}
AWAITING_FILES: Dict[str, object] = {}
