# handlers.py
import os
import asyncio
import logging
import subprocess
from uuid import uuid4
from datetime import datetime
from telegram import Update, InputFile
from telegram.ext import ContextTypes
from telegram.constants import ChatType

from config import OWNER_ID, CACHE_CHAT_ID, MAX_TG_SIZE, SMART_FMT_1080, MAX_PARALLEL
from downloader import download_video_smart
from video_processing import get_video_info, generate_thumbnail
from userbot import send_via_userbot
from formats import build_full_format_keyboard
from utils import run_io, format_bytes

# Глобальные объекты
DOWNLOAD_TASKS: dict[str, str] = {}
AWAITING_FILES: dict[str, asyncio.Future] = {}
DL_SEM = asyncio.Semaphore(MAX_PARALLEL)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    await update.message.reply_text("Используй инлайн @бота или пришли ссылку")


async def send_file_ids(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Хендлер: прислали медиа -> вернуть file_id (только для OWNER)"""
    if not update.effective_user or update.effective_user.id != OWNER_ID:
        return

    msg = update.effective_message
    parts = []

    def add(label, obj):
        if obj:
            parts.append(
                f"{label} file_id:\n{obj.file_id}\n{label} unique_id:\n{obj.file_unique_id}"
            )

    if msg.photo:
        add("photo", msg.photo[-1])

    add("document", msg.document)
    add("animation", msg.animation)
    add("video", msg.video)
    add("sticker", msg.sticker)
    add("audio", msg.audio)
    add("voice", msg.voice)
    add("video_note", msg.video_note)

    if not parts:
        return

    text = "⚙️ Нашёл ID:\n\n" + "\n\n".join(parts)
    await msg.reply_text(text)
    print(text)


async def cache_listener(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ловит видео в кеш-чате и резолвит file_id"""
    msg = update.effective_message
    if not msg or not msg.video:
        return

    v = msg.video
    unique = v.file_unique_id
    from_chat = update.effective_chat
    chat_info = f"{from_chat.type} {from_chat.id} ({getattr(from_chat, 'username', '')})"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if unique in AWAITING_FILES:
        logging.info(f"[CACHE] {ts} — Видео получено. chat={chat_info} unique_id={unique} file_id={v.file_id}")
        fut = AWAITING_FILES.get(unique)
        if fut and not fut.done():
            fut.set_result((v.file_id, v.duration or 0, v.width or 0, v.height or 0))
    else:
        logging.info(f"[CACHE] {ts} — Видео не запрашивалось. chat={chat_info} unique_id={unique} file_id={v.file_id}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений со ссылками"""
    text = update.message.text or ""
    chat_type = update.message.chat.type

    if chat_type in ("group", "supergroup"):
        bot_username = context.bot.username
        if f"@{bot_username}" not in text:
            return
        text = text.replace(f"@{bot_username}", "").strip()

    url = text
    logging.info(f"[БОТ] Ссылка: {url}")
    status = await update.message.reply_text("Скачиваю...")

    video_path = None
    try:
        async with DL_SEM:
            video_path = await run_io(download_video_smart, url)

        size = os.path.getsize(video_path)
        logging.info(f"[SEND] Итоговый файл {format_bytes(size)} (лимит {format_bytes(MAX_TG_SIZE)})")

        if size > MAX_TG_SIZE:
            logging.info("[SEND] >50MB — отправляем через юзербота")
            file_id, duration, width, height = await send_via_userbot(
                video_path, caption=f"Кэширование… {url}", bot=context.bot
            )
            await status.edit_text("Готово!")
            await update.message.reply_video(video=file_id, caption=f"Видео готово: {url}")
        else:
            await status.edit_text("Готово!")
            await update.message.reply_video(video=open(video_path, "rb"), caption=f"Видео готово: {url}")

    except subprocess.CalledProcessError as e:
        err = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or str(e))
        logging.error(f"[БОТ] Ошибка subprocess: {err}")

        if "Requested format is not available" in err or "--list-formats" in err:
            task = uuid4().hex[:8]
            DOWNLOAD_TASKS[task] = url
            kb = build_full_format_keyboard(task, url)
            await status.edit_text("Этот формат недоступен.\nВыбери другой формат или GIF:", reply_markup=kb)
        else:
            await status.edit_text(f"Ошибка: {err}")

    except Exception as e:
        logging.error(f"[БОТ] Ошибка: {e}")
        await status.edit_text(f"Ошибка: {e}")
    finally:
        try:
            if video_path and os.path.exists(video_path):
                os.remove(video_path)
        except Exception:
            pass
