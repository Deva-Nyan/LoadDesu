#pyro_send.py
import os, logging
from typing import Optional
import state  # <-- читаем живые значения
from config import CACHE_CHAT_ID, CACHE_THREAD_ID
from services.video import get_video_info, generate_thumbnail
from utils.threading import run_io  # если у тебя есть обертка

async def send_via_userbot(video_path: str, caption: Optional[str] = None, bot=None):
    if bot is None:
        raise RuntimeError("Нужно передать bot (context.bot).")
    app = await state.get_pyro_app()

    duration, width, height = await run_io(get_video_info, video_path)
    thumb = await run_io(generate_thumbnail, video_path)

    base_kwargs = dict(
        caption=caption or "",
        supports_streaming=True,
        width=width, height=height, duration=duration,
    )
    if thumb and os.path.exists(thumb):
        base_kwargs["thumb"] = thumb

    # 1) DM боту (identity обязателен)
    dm_chat = f"@{state.BOT_USERNAME}" if state.BOT_USERNAME else state.BOT_ID
    if not dm_chat:
        raise RuntimeError("BOT_USERNAME/BOT_ID не заданы. Вызови set_bot_identity() на старте.")

    try:
        await app.send_message(dm_chat, "/start")
        logging.info("[PYRO→DM] /start отправлен")
    except Exception as e:
        logging.info(f"[PYRO→DM] /start: {e}")

    msg_dm = await app.send_video(chat_id=dm_chat, video=video_path, **base_kwargs)
    logging.info(f"[PYRO→DM] Видео отправлено. unique_id={msg_dm.video.file_unique_id}")

    # 2) Дубликат в кэш-чат (если нужен тред через Pyrogram — используем reply_to_message_id)
    cache_kwargs = dict(**base_kwargs)
    if CACHE_THREAD_ID:
        cache_kwargs["reply_to_message_id"] = CACHE_THREAD_ID  # <-- Pyrogram way

    msg_cache = await app.send_video(chat_id=CACHE_CHAT_ID, video=video_path, **cache_kwargs)
    logging.info(f"[PYRO→CACHE] Дубликат отправлен. message_id={msg_cache.id}")

    # 3) Забираем file_id через PTB-бота
    copied = await bot.forward_message(
        chat_id=CACHE_CHAT_ID,
        from_chat_id=CACHE_CHAT_ID,
        message_id=msg_cache.id,
        message_thread_id=CACHE_THREAD_ID if CACHE_THREAD_ID else None,
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
