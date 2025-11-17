# callback_handlers.py
import os
import logging
from telegram import Update, InputMediaVideo, InputMediaAudio, InputMediaAnimation, InputFile
from telegram.ext import ContextTypes
from telegram.error import BadRequest

from handlers import DOWNLOAD_TASKS, DL_SEM
from formats import build_full_format_keyboard
from database import cache_get, cache_put
from utils import run_io, get_content_key_and_title, detect_media_kind_and_key, extract_title_artist, is_url_allowed
from config import CACHE_CHAT_ID, CACHE_THREAD_ID, MAX_TG_SIZE, SMART_FMT_1080, ALLOWED_HOSTS

# Импорты функций для загрузки/обработки
from downloader import (download_video_with_format, download_animation_source, 
                        download_audio, download_video_smart)
from video_processing import video_to_tg_animation, get_video_info, generate_thumbnail
from userbot import send_via_userbot


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Главный обработчик callback-кнопок"""
    query = update.callback_query
    await query.answer()

    data = query.data
    parts = data.split("|")
    action = parts[0]
    task_id = parts[1]
    url = DOWNLOAD_TASKS.get(task_id)
    inline_id = query.inline_message_id

    if not url:
        await query.edit_message_caption(caption="Ошибка: ссылка устарела или не найдена.")
        return

    logging.info(f"[BTN] action={action} task={task_id} url={url}")

    if not is_url_allowed(url):
        allowed = ", ".join(host for host in ALLOWED_HOSTS) if ALLOWED_HOSTS else ""
        await _set_caption("Сайт не разрешён к загрузке." + (f"\nРазрешены: {allowed}" if allowed else ""))
        return

    # Helper для обновления подписи
    async def _set_caption(text: str, kb=None):
        try:
            if query.message:
                await context.bot.edit_message_caption(
                    chat_id=query.message.chat_id,
                    message_id=query.message.message_id,
                    caption=text,
                    reply_markup=kb
                )
            else:
                await context.bot.edit_message_caption(
                    inline_message_id=inline_id,
                    caption=text,
                    reply_markup=kb
                )
            logging.info(f"[BTN] caption set: {text[:80]}")
        except BadRequest as e:
            if "message is not modified" in str(e).lower():
                logging.info("[BTN] caption unchanged (noop)")
            else:
                logging.error(f"[BTN] edit_message_caption error: {e}")

    # Маршрутизация по action
    if action == "noop":
        return
    
    if action == "dl":
        kb = build_full_format_keyboard(task_id, url)
        await _set_caption(f"Выбери формат для: {url}", kb)
        return
    
    if action == "more":
        kb = build_full_format_keyboard(task_id, url)
        await _set_caption(f"Все форматы:", kb)
        return

    # Импортируем обработчики из других модулей
    from callback_actions import (handle_format_selection, handle_gif_action,
                                  handle_audio_action, handle_auto_action,
                                  handle_video_auto, handle_audio_auto)
    
    if action == "fmt":
        await handle_format_selection(query, context, task_id, url, parts[2], inline_id, _set_caption)
    elif action == "gif":
        await handle_gif_action(query, context, task_id, url, inline_id, _set_caption)
    elif action == "aud":
        await handle_audio_action(query, context, task_id, url, parts[2], inline_id, _set_caption)
    elif action == "auto":
        await handle_auto_action(query, context, task_id, url, inline_id, _set_caption)
    elif action == "vauto":
        await handle_video_auto(query, context, task_id, url, inline_id, _set_caption)
    elif action == "aauto":
        await handle_audio_auto(query, context, task_id, url, inline_id, _set_caption)
