import logging
from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes


async def cache_listener(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.video:
        return
    v = msg.video
    unique = v.file_unique_id
    from_chat = update.effective_chat
    chat_info = f"{from_chat.type} {from_chat.id} ({getattr(from_chat, 'username', '')})"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logging.info(f"[CACHE] {ts} — Видео {'получено' if unique else '??'} {chat_info} unique_id={unique} file_id={v.file_id}")