"""Telegram handlers implementing the bot's behaviour."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import List, Sequence

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InlineQueryResultCachedPhoto,
    InputFile,
    InputMediaVideo,
    InputTextMessageContent,
    Message,
)
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from config import (
    MAX_TG_SIZE,
    OWNER_ID,
    PLACEHOLDER_PHOTO_ID,
    SMART_FMT_1080,
)
from core.downloader import (
    DownloadError,
    collect_format_summary,
    delete_file_quietly,
    download_video,
    download_with_format,
    probe_video_details,
)
from core.formats import FormatSummary
from core.tasks import TASKS
from core.urls import extract_first_url, is_domain_allowed
from core.utils import format_bytes

# Domains that the bot is allowed to download content from.
ALLOWED_DOMAINS: Sequence[str] = (
    "youtube.com",
    "youtu.be",
    "vimeo.com",
    "dailymotion.com",
)

CALLBACK_AUTO = "auto"
CALLBACK_LIST = "list"
CALLBACK_MENU = "menu"
CALLBACK_FORMAT = "fmt"
CALLBACK_NOOP = "noop"


# What: Send a friendly help message when the user runs /start.
# Inputs: ``update``/``context`` provided by python-telegram-bot.
# Outputs: None; sends a reply to the chat.
async def start_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message:
        return
    await update.effective_message.reply_text(
        "Send me a supported video link and I will fetch it for you."
    )


# What: Let the owner inspect Telegram file IDs for caching purposes.
# Inputs: ``update``/``context`` and expects the OWNER_ID environment variable to be set.
# Outputs: None; replies with the collected file identifiers.
async def send_file_ids(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user or OWNER_ID <= 0 or user.id != OWNER_ID:
        return

    file_parts: List[str] = []

    def append(entity_label: str, media_object) -> None:
        if not media_object:
            return
        file_parts.append(
            f"{entity_label} file_id:\n{media_object.file_id}\n{entity_label} unique_id:\n{media_object.file_unique_id}"
        )

    append("photo", message.photo[-1] if message.photo else None)
    append("document", message.document)
    append("animation", message.animation)
    append("video", message.video)
    append("sticker", message.sticker)
    append("audio", message.audio)
    append("voice", message.voice)
    append("video_note", message.video_note)

    if not file_parts:
        return

    await message.reply_text("⚙️ Cached identifiers:\n\n" + "\n\n".join(file_parts))


# What: Process incoming messages, download supported URLs and send them back.
# Inputs: ``update``/``context`` – Telegram handler payload.
# Outputs: None; sends status updates and the resulting video/document.
async def handle_url_message(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return

    text = (message.text or message.caption or "").strip()
    if not text:
        return

    if _is_group_chat(message) and not _is_bot_mentioned(text, context.bot.username):
        return
    if _is_group_chat(message):
        text = _remove_bot_mention(text, context.bot.username)

    url = extract_first_url(text)
    if not url:
        return

    if not is_domain_allowed(url, ALLOWED_DOMAINS):
        await message.reply_text("This domain is not whitelisted for downloads.")
        return

    logging.info(
        "[MSG] user=%s chat=%s requested url=%s",
        getattr(update.effective_user, "id", "?"),
        getattr(message.chat, "id", "?"),
        url,
    )

    status = await message.reply_text("⏬ Preparing download…")
    download_path: Path | None = None
    try:
        start = time.monotonic()
        download_path = await asyncio.to_thread(download_video, url)
        file_size = download_path.stat().st_size
        elapsed = time.monotonic() - start
        logging.info(
            "[MSG] downloaded path=%s size=%s elapsed=%.1fs",
            download_path,
            format_bytes(file_size),
            elapsed,
        )

        if file_size > MAX_TG_SIZE:
            logging.warning("[MSG] file too large for Telegram: %s", format_bytes(file_size))
            await status.edit_text(
                "The file is larger than Telegram's upload limit (50 MB)."
            )
            return

        duration, width, height = await asyncio.to_thread(
            probe_video_details, download_path
        )
        with download_path.open("rb") as fp:
            await message.reply_video(
                video=InputFile(fp, filename=download_path.name),
                caption=f"Готово! {url}",
                duration=duration or None,
                width=width or None,
                height=height or None,
            )
        await status.edit_text("Done! 🎉")
    except DownloadError as exc:
        logging.warning("[MSG] default download failed for %s: %s", url, exc)
        await _present_alternative_formats(status, url, context, error_hint=str(exc))
    except Exception as exc:  # pragma: no cover - defensive fallback
        logging.exception("[MSG] unexpected error while handling %s", url)
        await status.edit_text(f"Unexpected error: {exc}")
    finally:
        if download_path:
            await asyncio.to_thread(delete_file_quietly, download_path)


# What: Provide inline results so users can fetch media via ``@bot`` mentions.
# Inputs: Telegram inline query payload.
# Outputs: Inline result containing a placeholder preview and keyboard.
async def handle_inline_query(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    inline_query = update.inline_query
    if not inline_query:
        return

    url = extract_first_url(inline_query.query or "")
    if not url:
        return

    if not is_domain_allowed(url, ALLOWED_DOMAINS):
        result = InlineQueryResultArticle(
            id="denied",
            title="Домен не в белом списке",
            input_message_content=InputTextMessageContent(
                "Этот домен не разрешён для загрузки."
            ),
        )
        await inline_query.answer([result], cache_time=0, is_personal=True)
        return

    uses_caption = bool(PLACEHOLDER_PHOTO_ID)
    task_id = TASKS.create(url, uses_caption=uses_caption)
    keyboard = _build_main_keyboard(task_id)

    if PLACEHOLDER_PHOTO_ID:
        result = InlineQueryResultCachedPhoto(
            id=task_id,
            photo_file_id=PLACEHOLDER_PHOTO_ID,
            caption=f"Ссылка: {url}",
            reply_markup=keyboard,
        )
    else:
        result = InlineQueryResultArticle(
            id=task_id,
            title="Скачать видео",
            input_message_content=InputTextMessageContent(f"Ссылка: {url}"),
            reply_markup=keyboard,
        )

    await inline_query.answer([result], cache_time=0, is_personal=True)
    logging.info("[INLINE] prepared task=%s url=%s", task_id, url)


# What: React to inline/callback button presses to start downloads or show menus.
# Inputs: Telegram callback query payload.
# Outputs: Updates the placeholder message and optionally sends the final media.
async def handle_callback_query(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return

    parts = query.data.split("|")
    if len(parts) < 2:
        return
    action, task_id = parts[0], parts[1]
    task = TASKS.get(task_id)
    if not task:
        await _answer_expired_task(query)
        return

    await query.answer()

    TASKS.attach_message(
        task_id,
        chat_id=getattr(query.message.chat, "id", None) if query.message else None,
        message_id=getattr(query.message, "message_id", None),
        inline_message_id=query.inline_message_id,
    )

    if action == CALLBACK_NOOP:
        return

    if action == CALLBACK_MENU:
        await _edit_task_message(
            context,
            task,
            f"Ссылка: {task.url}",
            reply_markup=_build_main_keyboard(task_id),
        )
        return

    if action == CALLBACK_LIST:
        await _show_format_list(context, task, task_id)
        return

    if action == CALLBACK_AUTO:
        await _start_download(context, task_id, SMART_FMT_1080, "автовыбор")
        return

    if action == CALLBACK_FORMAT and len(parts) >= 3:
        format_selector = parts[2]
        await _start_download(context, task_id, format_selector, format_selector)
        return


# What: Check if the message originated from a group or supergroup chat.
# Inputs: Telegram ``Message`` object.
# Outputs: ``True`` when group-based, ``False`` otherwise.
def _is_group_chat(message: Message) -> bool:
    chat_type = getattr(message.chat, "type", "")
    return chat_type in {"group", "supergroup"}


# What: Identify if the bot was mentioned in the given text.
# Inputs: ``text`` - message string; ``bot_username`` - username without @.
# Outputs: ``True`` if an ``@bot_username`` mention is present.
def _is_bot_mentioned(text: str, bot_username: str | None) -> bool:
    if not bot_username:
        return False
    mention = f"@{bot_username}".lower()
    return mention in text.lower()


# What: Remove bot mention occurrences from a text snippet.
# Inputs: ``text`` - original message; ``bot_username`` - username without @.
# Outputs: Message text with mentions stripped.
def _remove_bot_mention(text: str, bot_username: str | None) -> str:
    if not bot_username:
        return text
    mention = f"@{bot_username}"
    return text.replace(mention, "").strip()


async def _present_alternative_formats(
    status_message: Message,
    url: str,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    error_hint: str | None = None,
) -> None:
    """Offer a keyboard with alternative quality options after a failure."""

    task_id = TASKS.create(url, uses_caption=False)
    TASKS.attach_message(
        task_id,
        chat_id=status_message.chat_id,
        message_id=status_message.message_id,
    )

    summary = await _load_summary(url)
    if not summary:
        await status_message.edit_text(
            "Не удалось подобрать альтернативные форматы. Попробуйте позже."
        )
        TASKS.forget(task_id)
        return

    keyboard = _build_format_keyboard(task_id, summary, include_back=False)
    message_text = "Не удалось скачать этот формат. Выберите другой вариант:"
    if error_hint:
        message_text = f"Ошибка: {error_hint}\n\n" + message_text
    await status_message.edit_text(message_text, reply_markup=keyboard)
    logging.info("[MSG] offered alternatives for url=%s task=%s", url, task_id)


async def _show_format_list(
    context: ContextTypes.DEFAULT_TYPE, task, task_id: str
) -> None:
    """Fetch format metadata and display a detailed keyboard."""

    summary = await _load_summary(task.url)
    if not summary:
        await _edit_task_message(
            context,
            task,
            "Не удалось получить список форматов. Попробуйте позже.",
        )
        return

    keyboard = _build_format_keyboard(task_id, summary, include_back=True)
    await _edit_task_message(
        context,
        task,
        "Выберите формат для скачивания:",
        reply_markup=keyboard,
    )


async def _start_download(
    context: ContextTypes.DEFAULT_TYPE,
    task_id: str,
    format_selector: str,
    label: str,
) -> None:
    """Run yt-dlp for the selected format and deliver the result."""

    task = TASKS.get(task_id)
    if not task:
        return

    url = task.url
    await _edit_task_message(
        context,
        task,
        f"⏬ Скачиваю ({label})…",
    )

    download_path: Path | None = None
    try:
        start = time.monotonic()
        download_path = await asyncio.to_thread(
            download_with_format, url, format_selector
        )
        file_size = download_path.stat().st_size
        elapsed = time.monotonic() - start
        logging.info(
            "[DL] url=%s format=%s size=%s elapsed=%.1fs",
            url,
            format_selector,
            format_bytes(file_size),
            elapsed,
        )

        if file_size > MAX_TG_SIZE:
            logging.warning(
                "[DL] file exceeds Telegram limit for url=%s format=%s", url, format_selector
            )
            summary = await _load_summary(url)
            keyboard = (
                _build_format_keyboard(task_id, summary, include_back=True)
                if summary
                else None
            )
            await _edit_task_message(
                context,
                task,
                "Файл больше 50MB. Выберите вариант поменьше:",
                reply_markup=keyboard,
            )
            return

        duration, width, height = await asyncio.to_thread(
            probe_video_details, download_path
        )
        if task.inline_message_id and task.uses_caption:
            with download_path.open("rb") as fp:
                media = InputMediaVideo(
                    media=InputFile(fp, filename=download_path.name),
                    caption=f"Готово! {url}",
                    duration=duration or None,
                    width=width or None,
                    height=height or None,
                )
                await context.bot.edit_message_media(
                    inline_message_id=task.inline_message_id,
                    media=media,
                )
        elif task.chat_id and task.message_id:
            with download_path.open("rb") as fp:
                await context.bot.send_video(
                    chat_id=task.chat_id,
                    video=InputFile(fp, filename=download_path.name),
                    caption=f"Готово! {url}",
                    duration=duration or None,
                    width=width or None,
                    height=height or None,
                )
            await _edit_task_message(context, task, "Готово! ✅")
        else:
            logging.warning("[DL] task %s has no delivery target", task_id)
        TASKS.forget(task_id)
    except DownloadError as exc:
        logging.warning(
            "[DL] yt-dlp failed for url=%s format=%s: %s", url, format_selector, exc
        )
        summary = await _load_summary(url)
        keyboard = (
            _build_format_keyboard(task_id, summary, include_back=True)
            if summary
            else None
        )
        await _edit_task_message(
            context,
            task,
            f"Не удалось скачать: {exc}\nВыберите другой вариант:",
            reply_markup=keyboard,
        )
    except Exception as exc:  # pragma: no cover - defensive fallback
        logging.exception(
            "[DL] unexpected error for url=%s format=%s", url, format_selector
        )
        await _edit_task_message(
            context,
            task,
            f"Неожиданная ошибка: {exc}",
        )
    finally:
        if download_path:
            await asyncio.to_thread(delete_file_quietly, download_path)


async def _edit_task_message(
    context: ContextTypes.DEFAULT_TYPE,
    task,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Edit the status message associated with ``task`` safely."""

    try:
        if task.inline_message_id:
            if task.uses_caption:
                await context.bot.edit_message_caption(
                    inline_message_id=task.inline_message_id,
                    caption=text,
                    reply_markup=reply_markup,
                )
            else:
                await context.bot.edit_message_text(
                    inline_message_id=task.inline_message_id,
                    text=text,
                    reply_markup=reply_markup,
                )
        elif task.chat_id and task.message_id:
            await context.bot.edit_message_text(
                chat_id=task.chat_id,
                message_id=task.message_id,
                text=text,
                reply_markup=reply_markup,
            )
    except BadRequest as exc:  # pragma: no cover - user may spam buttons
        if "message is not modified" in str(exc).lower():
            logging.debug("[DL] message unchanged: %s", exc)
        else:
            logging.warning("[DL] failed to edit message: %s", exc)


async def _load_summary(url: str) -> FormatSummary | None:
    """Run yt-dlp metadata probe in a worker thread."""

    try:
        return await asyncio.to_thread(collect_format_summary, url)
    except DownloadError as exc:
        logging.warning("[META] failed to probe formats for %s: %s", url, exc)
        return None


def _build_main_keyboard(task_id: str) -> InlineKeyboardMarkup:
    """Return the minimal keyboard with auto-download and menu options."""

    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⚡ Автовыбор", callback_data=f"{CALLBACK_AUTO}|{task_id}")],
            [
                InlineKeyboardButton(
                    "🎛 Выбрать качество", callback_data=f"{CALLBACK_LIST}|{task_id}"
                )
            ],
        ]
    )


def _build_format_keyboard(
    task_id: str, summary: FormatSummary, *, include_back: bool
) -> InlineKeyboardMarkup:
    """Create a detailed keyboard listing progressive/video/audio formats."""

    rows: List[List[InlineKeyboardButton]] = []
    for option in summary.progressive:
        rows.append(
            [
                InlineKeyboardButton(
                    f"▶️ {option.label}",
                    callback_data=f"{CALLBACK_FORMAT}|{task_id}|{option.format_id}",
                )
            ]
        )

    if summary.video_only:
        rows.append(
            [
                InlineKeyboardButton(
                    "— видео без звука —",
                    callback_data=f"{CALLBACK_NOOP}|{task_id}",
                )
            ]
        )
        for option in summary.video_only:
            rows.append(
                [
                    InlineKeyboardButton(
                        f"🎞 {option.label}",
                        callback_data=f"{CALLBACK_FORMAT}|{task_id}|{option.format_id}",
                    )
                ]
            )

    if summary.audio_only:
        rows.append(
            [
                InlineKeyboardButton(
                    "— аудио —",
                    callback_data=f"{CALLBACK_NOOP}|{task_id}",
                )
            ]
        )
        for option in summary.audio_only:
            rows.append(
                [
                    InlineKeyboardButton(
                        f"🎵 {option.label}",
                        callback_data=f"{CALLBACK_FORMAT}|{task_id}|{option.format_id}",
                    )
                ]
            )

    if include_back:
        rows.append(
            [
                InlineKeyboardButton(
                    "⬅️ Назад", callback_data=f"{CALLBACK_MENU}|{task_id}"
                )
            ]
        )

    if not rows:
        rows.append(
            [
                InlineKeyboardButton(
                    "Обновить список",
                    callback_data=f"{CALLBACK_LIST}|{task_id}",
                )
            ]
        )
    return InlineKeyboardMarkup(rows)


async def _answer_expired_task(query) -> None:
    """Notify the user that the inline button is no longer active."""

    try:
        await query.answer("Сессия истекла, попробуйте заново.", show_alert=True)
    except BadRequest:
        pass
