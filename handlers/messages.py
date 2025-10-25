import os, subprocess
import re, hashlib
import logging, json
import asyncio
import state
from telegram import Update, InputFile
from telegram.ext import ContextTypes

from config import SMART_FMT_1080, MAX_TG_SIZE, DL_SEM
from services.ytdlp import download_video_smart
from services.video import get_video_info, generate_thumbnail
from services.pyro_send import send_via_userbot
from services.content_key import get_content_key_and_title
from utils.text import format_bytes

# Регулярка для извлечения URL
URL_RE = re.compile(r"https?://\S+")

def detect_media_kind_and_key(url: str):
    """
    -> (mode, content_key, title)
    mode: 'video' | 'audio' | 'unknown'
    content_key в нижнем регистре.
    """
    try:
        info = ytdlp_info(url)
        fmts = info.get("formats", []) or []
        has_video = any(f.get("vcodec") not in (None, "none") for f in fmts)
        has_audio_only = any((f.get("vcodec") in (None, "none")) and (f.get("acodec") not in (None, "none")) for f in fmts)

        extractor = (info.get("extractor") or info.get("extractor_key") or "unknown").lower()
        vid = info.get("id") or ""

        if extractor == "youtube" and not vid:
            vid = extract_youtube_id(url) or ""

        key = f"{extractor}:{vid}" if vid else f"{extractor}:{hash(url)}"
        mode = "video" if has_video else ("audio" if has_audio_only else "unknown")
        logging.info(f"[AUTO/DETECT] {mode} key={key}")
        return mode, key, info.get("title")
    except Exception as e:
        logging.warning(f"[AUTO/DETECT] probe failed: {e}")
        key, title = get_content_key_and_title(url)
        return "unknown", key, title

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # игнорим сообщения, которые прислал аккаунт юзербота (чтобы не ловить свой же DM)
    if update.effective_user and state.USERBOT_ID and update.effective_user.id == state.USERBOT_ID:
        return
    
    msg = update.effective_message
    if not msg:
        # не Message-апдейт — просто игнор
        return

    # текст берём и из caption тоже (на случай пересланного/медийного сообщения)
    text = (msg.text or msg.caption or "").strip()
    if not text:
        return

    # тип чата тоже через effective_*
    chat = update.effective_chat
    chat_type = getattr(chat, "type", None)

    # в группах — реагируем только если упомянули бота
    if chat_type in ("group", "supergroup"):
        bot_username = context.bot.username
        if f"@{bot_username}" not in text:
            return
        text = text.replace(f"@{bot_username}", "").strip()

    url = text
    logging.info(f"[БОТ] Ссылка: {url}")
    status = await msg.reply_text("Скачиваю...")

    video_path = None
    try:
        async with DL_SEM:
            # выносим скачивание в отдельный поток
            video_path = await asyncio.to_thread(download_video_smart, url, SMART_FMT_1080)

        size = os.path.getsize(video_path)
        logging.info(f"[SEND] Итоговый файл {format_bytes(size)} (лимит {format_bytes(MAX_TG_SIZE)})")

        if size > MAX_TG_SIZE:
            logging.info("[SEND] >50MB — отправляем через юзербота")
            file_id, duration, width, height = await send_via_userbot(
                video_path, caption=f"Кэширование… {url}", bot=context.bot
            )
            await status.edit_text("Готово!")
            await update.effective_message.reply_video(video=file_id, caption=f"Видео готово: {url}")
        else:
            await status.edit_text("Готово!")
            duration, width, height = get_video_info(video_path)
            thumb = generate_thumbnail(video_path)
            await update.effective_message.reply_video(
                video=open(video_path, "rb"),
                caption=f"Видео готово: {url}",
                duration=duration,
                width=width,
                height=height,
                thumbnail=InputFile(thumb) if thumb else None,
            )

    except Exception as e:
        logging.error(f"[БОТ] Ошибка: {e}")
        await status.edit_text(f"Ошибка: {e}")

    finally:
        try:
            if video_path and os.path.exists(video_path):
                os.remove(video_path)
        except Exception:
            pass
