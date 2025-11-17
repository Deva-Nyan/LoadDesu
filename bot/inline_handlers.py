# inline_handlers.py
import logging
from uuid import uuid4
from telegram import Update, InlineQueryResultCachedPhoto, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from telegram.error import BadRequest
from telegram import InlineQueryResultArticle, InputTextMessageContent

from config import PLACEHOLDER_PHOTO_ID
from handlers import DOWNLOAD_TASKS


async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка inline-запросов"""
    url = update.inline_query.query.strip()
    if not url.startswith("http"):
        return

    task = uuid4().hex[:8]
    DOWNLOAD_TASKS[task] = url

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Автовыбор", callback_data=f"auto|{task}"),
         InlineKeyboardButton("🎬 Видео",     callback_data=f"vauto|{task}")],
        [InlineKeyboardButton("🎵 Аудио",     callback_data=f"aauto|{task}"),
         InlineKeyboardButton("➕ Больше",     callback_data=f"more|{task}")],
    ])

    result = InlineQueryResultCachedPhoto(
        id=task,
        photo_file_id=PLACEHOLDER_PHOTO_ID,
        caption=f"Ссылка: {url}",
        reply_markup=kb,
    )
    
    try:
        await update.inline_query.answer([result], cache_time=0, is_personal=True)
        logging.info(f"[INLINE] task={task} show mini-menu for {url}")
    except BadRequest as e:
        # если плейсхолдер невалиден — фоллбек на Article
        fallback = InlineQueryResultArticle(
            id=task,
            title="Скачать",
            input_message_content=InputTextMessageContent(f"Видео готовится: {url}"),
            reply_markup=kb,
        )
        await update.inline_query.answer([fallback], cache_time=0, is_personal=True)
        logging.info(f"[INLINE] task={task} fallback Article (placeholder invalid)")
