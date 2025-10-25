# handlers/inline.py
import logging
from uuid import uuid4
from telegram import InlineKeyboardMarkup, InlineKeyboardButton, InlineQueryResultCachedPhoto
from telegram.error import BadRequest
from telegram.ext import ContextTypes
from state import DOWNLOAD_TASKS
from config import PLACEHOLDER_PHOTO_ID

def _mini_kb(task: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⚡ Автовыбор", callback_data=f"auto|{task}"),
            InlineKeyboardButton("🎬 Видео",     callback_data=f"vauto|{task}"),
        ],
        [
            InlineKeyboardButton("🎵 Аудио",     callback_data=f"aauto|{task}"),
            InlineKeyboardButton("➕ Больше",     callback_data=f"more|{task}"),
        ],
    ])

async def inline_query(update, context: ContextTypes.DEFAULT_TYPE):
    url = (update.inline_query.query or "").strip()
    if not url.startswith("http"):
        return

    task = uuid4().hex[:8]
    DOWNLOAD_TASKS[task] = url

    kb = _mini_kb(task)

    # ВАЖНО: здесь используем валидный для ЭТОГО бота file_id!
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
        logging.error(f"[INLINE] CachedPhoto failed: {e}")
        # Можно ничего не отдавать; либо сделать Article-фоллбек,
        # но помни: Article нельзя потом превратить в медиа edit_message_media.
        # Лучше разобраться с плейсхолдером, чем падать в Article.
