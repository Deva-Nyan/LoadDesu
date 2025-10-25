# handlers/buttons.py
import os
import asyncio
import logging
import subprocess
from typing import Optional, Dict, Tuple

from telegram import InputMediaVideo, InputMediaAudio, InputMediaAnimation, InputFile
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from state import DOWNLOAD_TASKS

# === ваши сервисы ===
from services.video import get_video_info, generate_thumbnail, video_to_tg_animation
from services.ytdlp import download_video_with_format, download_video_smart, download_audio
from services.content_key import get_content_key_and_title, detect_media_kind_and_key, extract_title_artist, canon_key
from services.cache_db import cache_get, cache_put
from services.pyro_send import send_via_userbot

# ─────────────────────────────────────────────────────────
# Константы/настройки
from config import CACHE_CHAT_ID, CACHE_THREAD_ID, MAX_TG_SIZE, DL_SEM, SMART_FMT_1080, GIF_FMT

# Анти-дубли: один ключ (content_key, variant) — одно одновременное скачивание
INFLIGHT: Dict[Tuple[str, str], asyncio.Lock] = {}

def cache_get_any(content_key: str, variant: str):
    # пробуем канонический ключ
    k1 = canon_key(content_key)
    row = cache_get(k1, variant)
    if row:
        return row
    # пробуем альтернативу с другим регистром "youtube"/"YouTube"
    if ":" in content_key:
        prefix, rest = content_key.split(":", 1)
        alt_prefixes = {prefix.lower(), prefix.capitalize(), prefix.upper(), "YouTube", "youtube"}
        for p in alt_prefixes:
            k2 = f"{p}:{rest}"
            if k2 == k1:
                continue
            row = cache_get(k2, variant)
            if row:
                return row
    return None

def get_inflight_lock(content_key: str, variant: str) -> asyncio.Lock:
    k = (content_key, variant)
    lk = INFLIGHT.get(k)
    if not lk:
        lk = asyncio.Lock()
        INFLIGHT[k] = lk
    return lk


async def _run_io(func, *args, **kwargs):
    return await asyncio.to_thread(func, *args, **kwargs)


async def button_callback(update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = (query.data or "")
    await query.answer()

    try:
        parts = data.split("|")
        action = parts[0]
        task_id = parts[1] if len(parts) > 1 else ""
    except Exception:
        logging.error(f"[BTN] bad callback data: {data!r}")
        return

    url = DOWNLOAD_TASKS.get(task_id)
    inline_id = query.inline_message_id  # критично для инлайна!

    logging.info(f"[BTN] action={action} task={task_id} url={url} inline_id={inline_id}")

    if not url:
        # инлайн-пост живёт, а словарь перезапустился — показываем ошибку
        try:
            if inline_id:
                await context.bot.edit_message_caption(
                    inline_message_id=inline_id,
                    caption="Ошибка: ссылка устарела или не найдена."
                )
            else:
                await query.edit_message_caption(caption="Ошибка: ссылка устарела или не найдена.")
        except BadRequest as e:
            logging.error(f"[BTN] could not set error caption: {e}")
        return

    async def _set_caption(text: str, kb=None):
        try:
            if inline_id:
                await context.bot.edit_message_caption(
                    inline_message_id=inline_id,
                    caption=text, reply_markup=kb
                )
            else:
                await context.bot.edit_message_caption(
                    chat_id=query.message.chat_id,
                    message_id=query.message.message_id,
                    caption=text, reply_markup=kb
                )
            logging.info(f"[BTN] caption -> {text[:120]}")
        except BadRequest as e:
            if "message is not modified" in str(e).lower():
                logging.info("[BTN] caption noop")
            else:
                logging.error(f"[BTN] edit_message_caption fail: {e}")

    # ─────────────────────────────────────────────────────────
    # Служебные ветки
    if action == "noop":
        return

    if action == "more":
        from services.keyboard import build_full_format_keyboard  # импорт внутри, чтобы избежать циклических импортов
        kb = build_full_format_keyboard(task_id, url)
        await _set_caption(f"Все форматы для:\n{url}", kb)
        return

    # ─────────────────────────────────────────────────────────
    # Выбор конкретного формата
    if action == "fmt":
        fmt_id = parts[2] if len(parts) > 2 else None
        if not fmt_id:
            await _set_caption("Формат не распознан.")
            return

        content_key, title = get_content_key_and_title(url)
        variant = f"video:fmt={fmt_id}"

        # быстрый кеш-хит
        row = cache_get_any(content_key, variant)
        if row:
            fid = row["file_id"]
            logging.info(f"[CACHE HIT] {content_key} [{variant}] → {fid}")
            try:
                await context.bot.edit_message_media(
                    inline_message_id=inline_id,
                    media=InputMediaVideo(media=fid, caption=f"Видео готово: {url}")
                )
            except BadRequest as e:
                logging.error(f"[BTN/fmt] edit media fail (cache): {e}")
            return

        # защита от дублей
        lock = get_inflight_lock(content_key, variant)
        async with lock:
            # повторная проверка кэша (вдруг другой поток уже скачал)
            row = cache_get_any(content_key, variant)
            if row:
                fid = row["file_id"]
                logging.info(f"[CACHE HIT/AFTER-LOCK] {content_key} [{variant}] → {fid}")
                try:
                    await context.bot.edit_message_media(
                        inline_message_id=inline_id,
                        media=InputMediaVideo(media=fid, caption=f"Видео готово: {url}")
                    )
                except BadRequest as e:
                    logging.error(f"[BTN/fmt] edit media fail (cache-after-lock): {e}")
                return

            await _set_caption(f"Скачиваю формат {fmt_id}…")
            video_path = thumb = None
            try:
                async with DL_SEM:
                    video_path = await _run_io(download_video_with_format, url, fmt_id)
                size = os.path.getsize(video_path)
                logging.info(f"[FMT] {fmt_id} → {size} bytes")
            except Exception as e:
                logging.error(f"[FMT] primary fail: {e}")
                try:
                    async with DL_SEM:
                        video_path = await _run_io(download_video_smart, url, SMART_FMT_1080)
                    size = os.path.getsize(video_path)
                    logging.info(f"[FMT] fallback SMART_FMT_1080 → {size} bytes")
                except Exception as e2:
                    logging.error(f"[FMT] fallback fail: {e2}")
                    await _set_caption("Не удалось скачать выбранный формат.")
                    return

            # отправляем и кешируем
            try:
                if size <= MAX_TG_SIZE:
                    duration, width, height = await _run_io(get_video_info, video_path)
                    thumb = await _run_io(generate_thumbnail, video_path)
                    sent = await context.bot.send_video(
                        chat_id=CACHE_CHAT_ID, message_thread_id=CACHE_THREAD_ID,
                        video=open(video_path, "rb"),
                        duration=duration, width=width, height=height,
                        thumbnail=InputFile(thumb) if thumb else None,
                        caption="Кэширование…",
                    )
                    file_id = sent.video.file_id
                    file_unique_id = sent.video.file_unique_id
                else:
                    file_id, duration, width, height = await send_via_userbot(
                        video_path, caption=f"Кэширование… {url}", bot=context.bot
                    )
                    file_unique_id = None

                cache_put(
                    content_key, variant, kind="video",
                    file_id=file_id, file_unique_id=file_unique_id,
                    width=width, height=height, duration=duration, size=size,
                    fmt_used=fmt_id, title=title, source_url=url
                )
                logging.info(f"[DB] saved {content_key} [{variant}] → {file_id}")

                await context.bot.edit_message_media(
                    inline_message_id=inline_id,
                    media=InputMediaVideo(media=file_id, caption=f"Видео готово: {url}")
                )
            except BadRequest as e:
                logging.error(f"[FMT] edit media fail: {e}")
                await _set_caption("Не удалось отправить видео. Выбери другой формат:")
            finally:
                try:
                    if thumb and os.path.exists(thumb):
                        os.remove(thumb)
                    if video_path and os.path.exists(video_path):
                        os.remove(video_path)
                except Exception:
                    pass
        return

    # ─────────────────────────────────────────────────────────
    # Авто (≤1080p) / только видео / только аудио
    if action in ("auto", "vauto", "aauto"):
        try:
            if action == "aauto":
                mode = "audio"
                content_key, title = get_content_key_and_title(url)
            elif action == "vauto":
                mode = "video"
                content_key, title = get_content_key_and_title(url)
            else:
                mode, content_key, title = detect_media_kind_and_key(url)

            logging.info(f"[AUTO] {action} mode={mode} key={content_key}")

            async def reply_cached(kind: str, file_id: str):
                media = InputMediaVideo(media=file_id, caption=f"Видео готово: {url}") if kind == "video" \
                        else InputMediaAudio(media=file_id, caption=f"Аудио готово: {url}")
                await context.bot.edit_message_media(inline_message_id=inline_id, media=media)

            # ── ВИДЕО ─────────────────────────────────────────
            if mode == "video":
                variant = "video:smart1080"

                # быстрый кеш
                row = cache_get_any(content_key, variant)
                if row:
                    fid = row["file_id"]
                    logging.info(f"[CACHE HIT] {content_key} [{variant}] → {fid}")
                    await reply_cached("video", fid)
                    return

                # защита от дублей
                lock = get_inflight_lock(content_key, variant)
                async with lock:
                    row = cache_get_any(content_key, variant)
                    if row:
                        fid = row["file_id"]
                        logging.info(f"[CACHE HIT/AFTER-LOCK] {content_key} [{variant}] → {fid}")
                        await reply_cached("video", fid)
                        return

                    await _set_caption("Скачиваю видео (≤1080p)…")
                    video_path = thumb = None
                    async with DL_SEM:
                        video_path = await _run_io(download_video_smart, url, SMART_FMT_1080)
                    size = os.path.getsize(video_path)
                    logging.info(f"[AUTO/VIDEO] downloaded size={size}")

                    if size <= MAX_TG_SIZE:
                        duration, width, height = await _run_io(get_video_info, video_path)
                        thumb = await _run_io(generate_thumbnail, video_path)
                        sent = await context.bot.send_video(
                            chat_id=CACHE_CHAT_ID, message_thread_id=CACHE_THREAD_ID,
                            video=open(video_path, "rb"),
                            duration=duration, width=width, height=height,
                            thumbnail=InputFile(thumb) if thumb else None,
                            caption="Кэширование…",
                        )
                        file_id = sent.video.file_id
                        file_unique_id = sent.video.file_unique_id
                    else:
                        file_id, duration, width, height = await send_via_userbot(
                            video_path, caption=f"Кэширование… {url}", bot=context.bot
                        )
                        file_unique_id = None

                    cache_put(
                        content_key, variant, kind="video",
                        file_id=file_id, file_unique_id=file_unique_id,
                        width=width, height=height, duration=duration, size=size,
                        fmt_used=SMART_FMT_1080, title=title, source_url=url
                    )
                    logging.info(f"[DB] saved {content_key} [{variant}] → {file_id}")

                    await reply_cached("video", file_id)

                    try:
                        if thumb and os.path.exists(thumb): os.remove(thumb)
                        if video_path and os.path.exists(video_path): os.remove(video_path)
                    except Exception:
                        pass
                return

            # ── АУДИО ─────────────────────────────────────────
            variant = "audio:mp3"

            # быстрый кеш
            row = cache_get_any(content_key, variant)
            if row:
                fid = row["file_id"]
                logging.info(f"[CACHE HIT] {content_key} [{variant}] → {fid}")
                await reply_cached("audio", fid)
                return

            # защита от дублей
            lock = get_inflight_lock(content_key, variant)
            async with lock:
                row = cache_get_any(content_key, variant)
                if row:
                    fid = row["file_id"]
                    logging.info(f"[CACHE HIT/AFTER-LOCK] {content_key} [{variant}] → {fid}")
                    await reply_cached("audio", fid)
                    return

                await _set_caption("Готовлю аудио (mp3)…")
                async with DL_SEM:
                    audio_path = await _run_io(download_audio, url, "mp3")
                title_full, artist = extract_title_artist(url, title)
                sent = await context.bot.send_audio(
                    chat_id=CACHE_CHAT_ID, message_thread_id=CACHE_THREAD_ID,
                    audio=open(audio_path, "rb"),
                    title=title_full, performer=artist,
                    caption=f"Аудио готово: {url}",
                )
                file_id = sent.audio.file_id
                cache_put(
                    content_key, variant, kind="audio",
                    file_id=file_id, file_unique_id=sent.audio.file_unique_id,
                    width=None, height=None, duration=getattr(sent.audio, "duration", None),
                    size=os.path.getsize(audio_path), fmt_used="mp3", title=title_full, source_url=url
                )
                logging.info(f"[DB] saved {content_key} [{variant}] → {file_id}")

                await reply_cached("audio", file_id)
        except Exception as e:
            logging.error(f"[AUTO] fail: {e}")
            from services.keyboard import build_full_format_keyboard
            kb = build_full_format_keyboard(task_id, url)
            await _set_caption("Не удалось автовыбрать. Выбери формат:", kb)
        finally:
            try:
                if 'audio_path' in locals() and audio_path and os.path.exists(audio_path):
                    os.remove(audio_path)
            except Exception:
                pass
        return

    # ─────────────────────────────────────────────────────────
    # GIF (тихий MP4 для sendAnimation)
    if action == "gif":
        content_key, title = get_content_key_and_title(url)
        variant = "anim:50"

        # быстрый кеш
        row = cache_get_any(content_key, variant)
        if row:
            fid = row["file_id"]
            logging.info(f"[CACHE HIT] {content_key} [{variant}] → {fid}")
            try:
                await context.bot.edit_message_media(
                    inline_message_id=inline_id,
                    media=InputMediaAnimation(media=fid, caption=f"GIF готова: {url}")
                )
            except BadRequest as e:
                logging.error(f"[GIF] edit media fail (cache): {e}")
            return

        # защита от дублей
        lock = get_inflight_lock(content_key, variant)
        async with lock:
            row = cache_get_any(content_key, variant)
            if row:
                fid = row["file_id"]
                logging.info(f"[CACHE HIT/AFTER-LOCK] {content_key} [{variant}] → {fid}")
                try:
                    await context.bot.edit_message_media(
                        inline_message_id=inline_id,
                        media=InputMediaAnimation(media=fid, caption=f"GIF готова: {url}")
                    )
                except BadRequest as e:
                    logging.error(f"[GIF] edit media fail (cache-after-lock): {e}")
                return

            await _set_caption("Готовлю GIF…")
            src = anim = None
            try:
                async with DL_SEM:
                    src = await _run_io(download_video_with_format, url, "bv*[height<=480]+ba/b[height<=480]/b")
                async with DL_SEM:
                    anim = await _run_io(video_to_tg_animation, src, 50)

                sent = await context.bot.send_animation(
                    chat_id=CACHE_CHAT_ID, message_thread_id=CACHE_THREAD_ID,
                    animation=open(anim, "rb"), caption=f"GIF готова: {url}",
                )
                file_id = sent.animation.file_id
                cache_put(
                    content_key, variant, kind="animation",
                    file_id=file_id, file_unique_id=sent.animation.file_unique_id,
                    width=sent.animation.width, height=sent.animation.height,
                    duration=sent.animation.duration, size=os.path.getsize(anim),
                    fmt_used="anim50", title=title, source_url=url
                )
                logging.info(f"[DB] saved {content_key} [{variant}] → {file_id}")

                await context.bot.edit_message_media(
                    inline_message_id=inline_id,
                    media=InputMediaAnimation(media=file_id, caption=f"GIF готова: {url}")
                )
            except Exception as e:
                logging.error(f"[GIF] fail: {e}")
                await _set_caption("Не удалось получить GIF.")
            finally:
                for p in (src, anim):
                    try:
                        if p and os.path.exists(p): os.remove(p)
                    except Exception:
                        pass
        return

    # ─────────────────────────────────────────────────────────
    await _set_caption("Неизвестная команда.")
