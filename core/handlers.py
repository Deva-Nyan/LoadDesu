"""Telegram handlers implementing the bot's behaviour."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import List, Sequence

from telegram import InputFile, Message
from telegram.ext import ContextTypes

from config import MAX_TG_SIZE, OWNER_ID
from core.downloader import DownloadError, delete_file_quietly, download_video, probe_video_details
from core.urls import extract_first_url, is_domain_allowed
from core.utils import format_bytes

# Domains that the bot is allowed to download content from.
ALLOWED_DOMAINS: Sequence[str] = ("youtube.com", "youtu.be", "vimeo.com", "dailymotion.com")


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

    status = await message.reply_text("Downloading…")
    download_path: Path | None = None
    try:
        download_path = await asyncio.to_thread(download_video, url)
        file_size = download_path.stat().st_size
        logging.info("Downloaded %s (%s)", download_path, format_bytes(file_size))

        if file_size > MAX_TG_SIZE:
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
                caption="Here is your video!",
                duration=duration or None,
                width=width or None,
                height=height or None,
            )
        await status.edit_text("Done! 🎉")
    except DownloadError as exc:
        logging.exception("Download failed")
        await status.edit_text(f"Download failed: {exc}")
    except Exception as exc:  # pragma: no cover - defensive fallback
        logging.exception("Unexpected error while handling message")
        await status.edit_text(f"Unexpected error: {exc}")
    finally:
        if download_path:
            await asyncio.to_thread(delete_file_quietly, download_path)


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
