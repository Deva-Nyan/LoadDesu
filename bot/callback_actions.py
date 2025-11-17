# callback_actions.py (часть 1 - fmt, gif, audio)
import os
import subprocess
import logging
from telegram import InputMediaVideo, InputMediaAudio, InputMediaAnimation, InputFile

from handlers import DL_SEM
from database import cache_get, cache_put
from utils import run_io, get_content_key_and_title, extract_title_artist, format_bytes
from config import CACHE_CHAT_ID, CACHE_THREAD_ID, MAX_TG_SIZE, SMART_FMT_1080
from downloader import download_video_with_format, download_animation_source, download_audio
from video_processing import video_to_tg_animation, video_to_gif, get_video_info, generate_thumbnail
from userbot import send_via_userbot
from formats import build_full_format_keyboard


async def handle_format_selection(query, context, task_id, url, fmt_id, inline_id, _set_caption):
    """Обработка выбора конкретного формата"""
    content_key, title = get_content_key_and_title(url)
    variant = f"video:fmt={fmt_id}"

    # Проверка кеша
    row = cache_get(content_key, variant)
    if row:
        logging.info(f"[CACHE HIT] {content_key} [{variant}]")
        await context.bot.edit_message_media(
            inline_message_id=inline_id,
            media=InputMediaVideo(media=row["file_id"], caption=f"Видео готово: {url}")
        )
        return

    await _set_caption(f"Скачиваю формат {fmt_id}…")
    video_path = None
    thumb = None
    
    try:
        # Скачиваем выбранный формат
        async with DL_SEM:
            video_path = await run_io(download_video_with_format, url, fmt_id)
        size = os.path.getsize(video_path)
        logging.info(f"[FMT] {fmt_id} → {format_bytes(size)}: {video_path}")

    except subprocess.CalledProcessError as e:
        err = e.stderr if isinstance(e.stderr, str) else (e.stderr.decode() if e.stderr else str(e))
        logging.error(f"[FMT] yt-dlp error for {fmt_id}: {err}")
        # Фоллбэк: общий профиль ≤1080p
        try:
            async with DL_SEM:
                video_path = await run_io(download_video_smart, url, fmt=SMART_FMT_1080)
            size = os.path.getsize(video_path)
            logging.info(f"[FMT] fallback SMART1080 → {format_bytes(size)}")
        except Exception as e2:
            await _set_caption("Не удалось скачать выбранный формат.")
            return
    except Exception as e:
        logging.error(f"[FMT] unexpected fail: {e}")
        await _set_caption("Не удалось скачать выбранный формат.")
        return

    # Отправка и кеш
    try:
        if size <= MAX_TG_SIZE:
            duration, width, height = await run_io(get_video_info, video_path)
            thumb = await run_io(generate_thumbnail, video_path)
            sent = await context.bot.send_video(
                chat_id=CACHE_CHAT_ID,
                message_thread_id=CACHE_THREAD_ID,
                video=open(video_path, "rb"),
                duration=duration, width=width, height=height,
                thumbnail=InputFile(thumb) if thumb else None,
                caption="Кэширование…",
            )
            file_id = sent.video.file_id
            file_unique_id = sent.video.file_unique_id
            logging.info(f"[FMT] sent via BOT → file_id={file_id}")
        else:
            file_id, duration, width, height = await send_via_userbot(
                video_path, caption=f"Кэширование… {url}", bot=context.bot
            )
            file_unique_id = None
            logging.info(f"[FMT] sent via USERBOT → file_id={file_id}")

        cache_put(
            content_key, variant, kind="video",
            file_id=file_id, file_unique_id=file_unique_id,
            width=width, height=height, duration=duration, size=size,
            fmt_used=fmt_id, title=title, source_url=url
        )
        logging.info(f"[CACHE SAVE] {content_key} [{variant}]")

        await context.bot.edit_message_media(
            inline_message_id=inline_id,
            media=InputMediaVideo(media=file_id, caption=f"Видео готово: {url}")
        )

    except Exception as e:
        logging.error(f"[FMT] send/edit fail: {e}")
        await _set_caption("Не удалось отправить видео. Выбери другой формат:")
    finally:
        try:
            if thumb and os.path.exists(thumb): os.remove(thumb)
            if video_path and os.path.exists(video_path): os.remove(video_path)
        except Exception:
            pass


async def handle_gif_action(query, context, task_id, url, inline_id, _set_caption):
    """Обработка GIF/Animation"""
    content_key, title = get_content_key_and_title(url)
    variant = "anim:50"

    row = cache_get(content_key, variant)
    if row:
        logging.info(f"[CACHE HIT] {content_key} [{variant}]")
        await context.bot.edit_message_media(
            inline_message_id=inline_id,
            media=InputMediaAnimation(media=row["file_id"], caption=f"GIF готова: {url}")
        )
        return

    await _set_caption("Готовлю GIF-анимацию…")
    src_path = None
    anim_path = None
    
    try:
        async with DL_SEM:
            src_path = await run_io(download_animation_source, url)
        logging.info(f"[ANIM] source: {src_path} ({format_bytes(os.path.getsize(src_path))})")

        async with DL_SEM:
            anim_path = await run_io(video_to_tg_animation, src_path, target_mb=50)
        anim_size = os.path.getsize(anim_path)
        logging.info(f"[ANIM] ready: {anim_path} ({format_bytes(anim_size)})")

        if query.message:
            sent = await context.bot.send_animation(
                chat_id=query.message.chat_id,
                animation=open(anim_path, "rb"),
                caption=f"GIF готова: {url}",
            )
            file_id = sent.animation.file_id
            file_unique_id = sent.animation.file_unique_id
            width = sent.animation.width
            height = sent.animation.height
            duration = sent.animation.duration
            
            await context.bot.edit_message_media(
                inline_message_id=inline_id,
                media=InputMediaAnimation(media=file_id, caption=f"GIF готова: {url}")
            )
        else:
            sent = await context.bot.send_animation(
                chat_id=CACHE_CHAT_ID,
                message_thread_id=CACHE_THREAD_ID,
                animation=open(anim_path, "rb"),
                caption=f"GIF готова: {url}",
            )
            file_id = sent.animation.file_id
            file_unique_id = sent.animation.file_unique_id
            width = sent.animation.width
            height = sent.animation.height
            duration = sent.animation.duration
            
            await context.bot.edit_message_media(
                inline_message_id=inline_id,
                media=InputMediaAnimation(media=file_id, caption=f"GIF готова: {url}")
            )

        cache_put(
            content_key, variant, kind="animation",
            file_id=file_id, file_unique_id=file_unique_id,
            width=width, height=height, duration=duration, size=anim_size,
            fmt_used="anim50", title=title, source_url=url
        )
        logging.info(f"[CACHE SAVE] {content_key} [{variant}] → {file_id}")

    except subprocess.CalledProcessError as e:
        err = e.stderr if isinstance(e.stderr, str) else (e.stderr.decode() if e.stderr else str(e))
        logging.error(f"[ANIM] yt-dlp/ffmpeg error: {err}")
        await _set_caption("Не удалось получить GIF.")
    except Exception as e:
        logging.error(f"[ANIM] fail: {e}")
        await _set_caption("Не удалось получить GIF.")
    finally:
        for p in (anim_path, src_path):
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass


async def handle_audio_action(query, context, task_id, url, aud_fmt, inline_id, _set_caption):
    """Обработка аудио (mp3/m4a)"""
    content_key, title = get_content_key_and_title(url)
    variant = f"audio:{aud_fmt}"

    row = cache_get(content_key, variant)
    if row:
        logging.info(f"[CACHE HIT] {content_key} [{variant}]")
        await context.bot.edit_message_media(
            inline_message_id=inline_id,
            media=InputMediaAudio(media=row["file_id"], caption=f"Аудио готово: {url}")
        )
        return

    await _set_caption(f"Готовлю аудио ({aud_fmt})…")
    audio_path = None
    
    try:
        async with DL_SEM:
            audio_path = await run_io(download_audio, url, aud_fmt)
        size = os.path.getsize(audio_path)

        title_full, artist = extract_title_artist(url, title)

        sent = await context.bot.send_audio(
            chat_id=CACHE_CHAT_ID,
            message_thread_id=CACHE_THREAD_ID,
            audio=open(audio_path, "rb"),
            title=title_full,
            performer=artist,
            caption=f"Аудио готово: {url}",
        )
        file_id = sent.audio.file_id
        file_unique_id = sent.audio.file_unique_id
        duration = getattr(sent.audio, "duration", None)

        cache_put(
            content_key, variant, kind="audio",
            file_id=file_id, file_unique_id=file_unique_id,
            width=None, height=None, duration=duration, size=size,
            fmt_used=aud_fmt, title=title_full, source_url=url
        )

        await context.bot.edit_message_media(
            inline_message_id=inline_id,
            media=InputMediaAudio(media=file_id, caption=f"Аудио готово: {url}")
        )
    except Exception as e:
        logging.error(f"[INLINE/AUD] fail: {e}")
        kb = build_full_format_keyboard(task_id, url)
        await _set_caption("Не удалось получить аудио.")
    finally:
        try:
            if audio_path and os.path.exists(audio_path):
                os.remove(audio_path)
        except Exception:
            pass

# callback_actions.py (часть 2 - auto actions)
# Добавить этот код в конец файла callback_actions.py

async def handle_video_auto(query, context, task_id, url, inline_id, _set_caption):
    """🎬 Видео: сразу ≤1080p (с кешем)"""
    from utils import get_content_key_and_title
    from database import cache_get, cache_put
    from downloader import download_video_smart
    from config import SMART_FMT_1080, MAX_TG_SIZE, CACHE_CHAT_ID, CACHE_THREAD_ID
    from handlers import DL_SEM
    
    content_key, title = get_content_key_and_title(url)
    variant = "video:smart1080"

    row = cache_get(content_key, variant)
    if row:
        logging.info(f"[CACHE HIT] {content_key} [{variant}]")
        await context.bot.edit_message_media(
            inline_message_id=inline_id,
            media=InputMediaVideo(media=row["file_id"], caption=f"Видео готово: {url}")
        )
        return

    await _set_caption("Скачиваю видео (≤1080p)…")
    video_path = None
    thumb = None
    
    try:
        async with DL_SEM:
            video_path = await run_io(download_video_smart, url, fmt=SMART_FMT_1080)
        size = os.path.getsize(video_path)
        logging.info(f"[VIDEO] downloaded {format_bytes(size)} → {video_path}")

        if size <= MAX_TG_SIZE:
            duration, width, height = await run_io(get_video_info, video_path)
            thumb = await run_io(generate_thumbnail, video_path)
            sent = await context.bot.send_video(
                chat_id=CACHE_CHAT_ID,
                message_thread_id=CACHE_THREAD_ID,
                video=open(video_path, "rb"),
                duration=duration, width=width, height=height,
                thumbnail=InputFile(thumb) if thumb else None,
                caption="Кэширование…",
            )
            file_id = sent.video.file_id
            file_unique_id = sent.video.file_unique_id
            logging.info(f"[VIDEO] sent via BOT → file_id={file_id}")
        else:
            file_id, duration, width, height = await send_via_userbot(
                video_path, caption=f"Кэширование… {url}", bot=context.bot
            )
            file_unique_id = None
            logging.info(f"[VIDEO] sent via USERBOT → file_id={file_id}")

        cache_put(
            content_key, variant, kind="video",
            file_id=file_id, file_unique_id=file_unique_id,
            width=width, height=height, duration=duration, size=size,
            fmt_used=SMART_FMT_1080, title=title, source_url=url
        )
        logging.info(f"[CACHE SAVE] {content_key} [{variant}]")

        await context.bot.edit_message_media(
            inline_message_id=inline_id,
            media=InputMediaVideo(media=file_id, caption=f"Видео готово: {url}")
        )

    except Exception as e:
        logging.error(f"[VIDEO] fail: {e}")
        await _set_caption("Не удалось скачать видео.")
    finally:
        try:
            if thumb and os.path.exists(thumb):
                os.remove(thumb)
            if video_path and os.path.exists(video_path):
                os.remove(video_path)
        except Exception:
            pass


async def handle_audio_auto(query, context, task_id, url, inline_id, _set_caption):
    """🎵 Аудио: сразу best (mp3)"""
    from utils import get_content_key_and_title, extract_title_artist
    from database import cache_get, cache_put
    from downloader import download_audio
    from config import CACHE_CHAT_ID, CACHE_THREAD_ID
    from handlers import DL_SEM
    from formats import build_full_format_keyboard
    
    content_key, title = get_content_key_and_title(url)
    variant = "audio:mp3"

    row = cache_get(content_key, variant)
    if row:
        logging.info(f"[CACHE HIT] {content_key} [{variant}]")
        await context.bot.edit_message_media(
            inline_message_id=inline_id,
            media=InputMediaAudio(media=row["file_id"], caption=f"Аудио готово: {url}")
        )
        return

    await _set_caption("Готовлю аудио (mp3)…")
    audio_path = None
    
    try:
        async with DL_SEM:
            audio_path = await run_io(download_audio, url, fmt="mp3")
        size = os.path.getsize(audio_path)

        title_full, artist = extract_title_artist(url, title)

        sent = await context.bot.send_audio(
            chat_id=CACHE_CHAT_ID,
            message_thread_id=CACHE_THREAD_ID,
            audio=open(audio_path, "rb"),
            title=title_full,
            performer=artist,
            caption=f"Аудио готово: {url}",
        )
        file_id = sent.audio.file_id
        file_unique_id = sent.audio.file_unique_id
        duration = getattr(sent.audio, "duration", None)

        cache_put(
            content_key, variant, kind="audio",
            file_id=file_id, file_unique_id=file_unique_id,
            width=None, height=None, duration=duration, size=size,
            fmt_used="mp3", title=title_full, source_url=url
        )

        await context.bot.edit_message_media(
            inline_message_id=inline_id,
            media=InputMediaAudio(media=file_id, caption=f"Аудио готово: {url}")
        )
    except subprocess.CalledProcessError as e:
        err = e.stderr if isinstance(e.stderr, str) else (e.stderr.decode() if e.stderr else str(e))
        logging.error(f"[AUDIO] yt-dlp error: {err}")
        kb = build_full_format_keyboard(task_id, url)
        await _set_caption("Не удалось получить аудио. Выбери вариант:", kb)
    except Exception as e:
        logging.error(f"[AUDIO] fail: {e}")
        kb = build_full_format_keyboard(task_id, url)
        await _set_caption("Не удалось получить аудио. Выбери вариант:", kb)
    finally:
        try:
            if audio_path and os.path.exists(audio_path):
                os.remove(audio_path)
        except Exception:
            pass


async def handle_auto_action(query, context, task_id, url, inline_id, _set_caption):
    """⚡ Автовыбор: определяет тип медиа и скачивает"""
    from utils import detect_media_kind_and_key
    
    mode, content_key, title = detect_media_kind_and_key(url)
    logging.info(f"[AUTO] mode={mode} key={content_key} url={url}")

    async def _reply_cached(kind: str, file_id: str):
        if kind == "video":
            await context.bot.edit_message_media(
                inline_message_id=inline_id,
                media=InputMediaVideo(media=file_id, caption=f"Видео готово: {url}")
            )
        elif kind == "audio":
            await context.bot.edit_message_media(
                inline_message_id=inline_id,
                media=InputMediaAudio(media=file_id, caption=f"Аудио готово: {url}")
            )

    if mode == "video":
        await _handle_auto_video(query, context, url, content_key, title, inline_id, _set_caption, _reply_cached, task_id)
    elif mode == "audio":
        await _handle_auto_audio(query, context, url, content_key, title, inline_id, _set_caption, _reply_cached, task_id)
    else:
        await _handle_auto_unknown(query, context, url, content_key, title, inline_id, _set_caption, _reply_cached, task_id)


# Вспомогательные функции для auto
async def _handle_auto_video(query, context, url, content_key, title, inline_id, _set_caption, _reply_cached, task_id):
    """Автовыбор: видео"""
    from database import cache_get, cache_put
    from config import SMART_FMT_1080, MAX_TG_SIZE, CACHE_CHAT_ID, CACHE_THREAD_ID
    from handlers import DL_SEM
    from formats import build_full_format_keyboard
    from downloader import download_video_smart, download_audio
    
    variant = "video:smart1080"
    row = cache_get(content_key, variant)
    if row:
        logging.info(f"[CACHE HIT] {content_key} [{variant}]")
        await _reply_cached("video", row["file_id"])
        return

    await _set_caption("Скачиваю (автовыбор: видео ≤1080p)…")
    video_path = thumb = None
    
    try:
        async with DL_SEM:
            video_path = await run_io(download_video_smart, url, fmt=SMART_FMT_1080)
        size = os.path.getsize(video_path)
        logging.info(f"[AUTO/VIDEO] downloaded {format_bytes(size)} → {video_path}")

        if size <= MAX_TG_SIZE:
            duration, width, height = await run_io(get_video_info, video_path)
            thumb = await run_io(generate_thumbnail, video_path)
            sent = await context.bot.send_video(
                chat_id=CACHE_CHAT_ID,
                message_thread_id=CACHE_THREAD_ID,
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
        await _reply_cached("video", file_id)

    except Exception as e:
        logging.error(f"[AUTO/VIDEO] fail: {e} — переключаюсь на аудио")
        # Фоллбэк на аудио
        await _fallback_to_audio(context, url, content_key, title, inline_id, _reply_cached, task_id, _set_caption)
    finally:
        try:
            if thumb and os.path.exists(thumb): os.remove(thumb)
            if video_path and os.path.exists(video_path): os.remove(video_path)
        except: pass


async def _handle_auto_audio(query, context, url, content_key, title, inline_id, _set_caption, _reply_cached, task_id):
    """Автовыбор: аудио"""
    from database import cache_get
    from handlers import DL_SEM
    
    variant = "audio:mp3"
    row = cache_get(content_key, variant)
    if row:
        logging.info(f"[CACHE HIT] {content_key} [{variant}]")
        await _reply_cached("audio", row["file_id"])
        return

    await _set_caption("Скачиваю (автовыбор: аудио)…")
    
    try:
        await _download_and_send_audio(context, url, content_key, title, inline_id, _reply_cached)
    except Exception as e:
        logging.error(f"[AUTO/AUDIO] fail: {e} — пробую видео")
        await _fallback_to_video(context, url, content_key, title, inline_id, _reply_cached, task_id, _set_caption)


async def _handle_auto_unknown(query, context, url, content_key, title, inline_id, _set_caption, _reply_cached, task_id):
    """Автовыбор: неизвестный тип (пробуем видео, потом аудио)"""
    from database import cache_get
    
    await _set_caption("Скачиваю (автовыбор)…")
    
    # Пробуем видео
    variant_v = "video:smart1080"
    row = cache_get(content_key, variant_v)
    if row:
        logging.info(f"[CACHE HIT] {content_key} [{variant_v}]")
        await _reply_cached("video", row["file_id"])
        return
    
    try:
        await _download_and_send_video(context, url, content_key, title, inline_id, _reply_cached)
        return
    except Exception as e:
        logging.error(f"[AUTO/UNKNOWN] video fail: {e} — пробую аудио")
    
    # Пробуем аудио
    variant_a = "audio:mp3"
    row = cache_get(content_key, variant_a)
    if row:
        logging.info(f"[CACHE HIT] {content_key} [{variant_a}]")
        await _reply_cached("audio", row["file_id"])
        return
    
    try:
        await _download_and_send_audio(context, url, content_key, title, inline_id, _reply_cached)
    except Exception as e2:
        logging.error(f"[AUTO/UNKNOWN] audio fail: {e2}")
        from formats import build_full_format_keyboard
        kb = build_full_format_keyboard(task_id, url)
        await _set_caption("Не удалось автовыбрать. Выбери формат:", kb)


async def _download_and_send_video(context, url, content_key, title, inline_id, _reply_cached):
    """Вспомогательная: скачать и отправить видео"""
    from config import SMART_FMT_1080, MAX_TG_SIZE, CACHE_CHAT_ID, CACHE_THREAD_ID
    from handlers import DL_SEM
    from database import cache_put
    from downloader import download_video_smart
    
    async with DL_SEM:
        video_path = await run_io(download_video_smart, url, fmt=SMART_FMT_1080)
    size = os.path.getsize(video_path)
    
    try:
        if size <= MAX_TG_SIZE:
            duration, width, height = await run_io(get_video_info, video_path)
            thumb = await run_io(generate_thumbnail, video_path)
            sent = await context.bot.send_video(
                chat_id=CACHE_CHAT_ID,
                message_thread_id=CACHE_THREAD_ID,
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
            content_key, "video:smart1080", kind="video",
            file_id=file_id, file_unique_id=file_unique_id,
            width=width, height=height, duration=duration, size=size,
            fmt_used=SMART_FMT_1080, title=title, source_url=url
        )
        await _reply_cached("video", file_id)
    finally:
        if 'thumb' in locals() and thumb and os.path.exists(thumb): os.remove(thumb)
        if video_path and os.path.exists(video_path): os.remove(video_path)


async def _download_and_send_audio(context, url, content_key, title, inline_id, _reply_cached):
    """Вспомогательная: скачать и отправить аудио"""
    from config import CACHE_CHAT_ID, CACHE_THREAD_ID
    from handlers import DL_SEM
    from database import cache_put
    from downloader import download_audio
    from utils import extract_title_artist
    
    async with DL_SEM:
        audio_path = await run_io(download_audio, url, fmt="mp3")
    
    try:
        title_full, artist = extract_title_artist(url, title)
        sent = await context.bot.send_audio(
            chat_id=CACHE_CHAT_ID,
            message_thread_id=CACHE_THREAD_ID,
            audio=open(audio_path, "rb"),
            title=title_full,
            performer=artist,
            caption=f"Аудио готово: {url}",
        )
        file_id = sent.audio.file_id
        cache_put(
            content_key, "audio:mp3", kind="audio",
            file_id=file_id, file_unique_id=sent.audio.file_unique_id,
            width=None, height=None, duration=None, size=os.path.getsize(audio_path),
            fmt_used="mp3", title=title, source_url=url
        )
        await _reply_cached("audio", file_id)
    finally:
        if audio_path and os.path.exists(audio_path): os.remove(audio_path)


async def _fallback_to_audio(context, url, content_key, title, inline_id, _reply_cached, task_id, _set_caption):
    """Фоллбэк: если видео не удалось, пробуем аудио"""
    from database import cache_get
    
    variant = "audio:mp3"
    row = cache_get(content_key, variant)
    if row:
        logging.info(f"[CACHE HIT] {content_key} [{variant}]")
        await _reply_cached("audio", row["file_id"])
        return
    
    try:
        await _download_and_send_audio(context, url, content_key, title, inline_id, _reply_cached)
    except Exception as e2:
        logging.error(f"[AUTO/FALLBACK-AUDIO] fail: {e2}")
        from formats import build_full_format_keyboard
        kb = build_full_format_keyboard(task_id, url)
        await _set_caption("Не удалось автовыбрать. Выбери формат:", kb)


async def _fallback_to_video(context, url, content_key, title, inline_id, _reply_cached, task_id, _set_caption):
    """Фоллбэк: если аудио не удалось, пробуем видео"""
    from database import cache_get
    
    variant_v = "video:smart1080"
    row = cache_get(content_key, variant_v)
    if row:
        logging.info(f"[CACHE HIT] {content_key} [{variant_v}]")
        await _reply_cached("video", row["file_id"])
        return
    
    try:
        await _download_and_send_video(context, url, content_key, title, inline_id, _reply_cached)
    except Exception as e2:
        logging.error(f"[AUTO/FALLBACK-VIDEO] fail: {e2}")
        from formats import build_full_format_keyboard
        kb = build_full_format_keyboard(task_id, url)
        await _set_caption("Не удалось автовыбрать. Выбери формат:", kb)
