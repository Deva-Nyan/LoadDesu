# userbot.py
import logging
import os
from typing import Optional
from pyrogram import Client as PyroClient
from config import (PYRO_API_ID, PYRO_API_HASH, PYRO_SESSION, 
                    CACHE_CHAT_ID, BOT_USERNAME, BOT_ID)
from video_processing import get_video_info, generate_thumbnail
from utils import run_io

pyro_app: Optional[PyroClient] = None


async def init_userbot():
    """Инициализация Pyrogram userbot"""
    global pyro_app
    
    if not PYRO_API_ID or not PYRO_API_HASH:
        logging.warning("[PYRO] PYRO_API_ID/PYRO_API_HASH не заданы — отправка через юзербота для >50MB не будет работать.")
        return
    
    logging.info("[PYRO] Стартуем Pyrogram…")
    logging.info(f"[PYRO] Будем использовать сессию: {PYRO_SESSION} (.session: {PYRO_SESSION+'.session'}) "
                 f"exists={os.path.exists(PYRO_SESSION+'.session')}")
    
    pyro_app = PyroClient(
        PYRO_SESSION,
        api_id=PYRO_API_ID,
        api_hash=PYRO_API_HASH,
        device_model="Google Pixel 7",
        system_version="Android 14",
        app_version="10.3.1",
    )
    await pyro_app.start()
    me = await pyro_app.get_me()
    logging.info(f"[PYRO] Запущен как @{getattr(me, 'username', None) or me.first_name} (id={me.id})")


async def stop_userbot():
    """Остановка Pyrogram userbot"""
    global pyro_app
    if pyro_app:
        logging.info("[PYRO] Останавливаем Pyrogram…")
        await pyro_app.stop()


async def send_via_userbot(video_path: str, caption: Optional[str] = None, bot=None):
    """Отправляет большие файлы через userbot"""
    if not pyro_app:
        raise RuntimeError("Pyrogram не запущен или не сконфигурирован.")
    if bot is None:
        raise RuntimeError("Нужно передать bot (context.bot).")
    
    duration, width, height = await run_io(get_video_info, video_path)
    thumb = await run_io(generate_thumbnail, video_path)
    
    base_kwargs = dict(
        caption=caption or "",
        supports_streaming=True,
        width=width, height=height, duration=duration,
    )
    if thumb and os.path.exists(thumb):
        base_kwargs["thumb"] = thumb

    # 1) юзербот отправляет в ЛС боту
    dm_chat = f"@{BOT_USERNAME}" if BOT_USERNAME else BOT_ID
    try:
        await pyro_app.send_message(dm_chat, "/start")
        logging.info("[PYRO→DM] /start отправлен боту")
    except Exception as e:
        logging.info(f"[PYRO→DM] /start: {e}")
    
    msg_dm = await pyro_app.send_video(chat_id=dm_chat, video=video_path, **base_kwargs)
    logging.info(f"[PYRO→DM] Видео отправлено. unique_id={msg_dm.video.file_unique_id}")

    # 2) юзербот дублирует в КЭШ-ЧАТ
    msg_cache = await pyro_app.send_video(chat_id=CACHE_CHAT_ID, video=video_path, **base_kwargs)
    logging.info(f"[PYRO→CACHE] Дубликат отправлен. message_id={msg_cache.id}")

    # 3) бот копирует ИМЕННО из КЭШ-ЧАТА
    copied = await bot.forward_message(
        chat_id=CACHE_CHAT_ID,
        from_chat_id=CACHE_CHAT_ID,
        message_id=msg_cache.id
    )
    v = copied.video
    bot_file_id = v.file_id

    logging.info(f"[BOT] Получен file_id: {bot_file_id}")

    try:
        if thumb and os.path.exists(thumb):
            os.remove(thumb)
    except Exception:
        pass

    return bot_file_id, (v.duration or duration or 0), (v.width or width or 0), (v.height or height or 0)
