#bot.py
import logging
from telegram import MessageEntity
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    InlineQueryHandler, CallbackQueryHandler, ContextTypes, filters
)

from config import TOKEN, OWNER_ID, CACHE_CHAT_ID
from state import set_bot_identity, get_pyro_app
from utils.filters import build_media_filter
from handlers.commands import start, id_cmd
from handlers.files_id import send_file_ids
from handlers.messages import handle_message
from handlers.inline import inline_query
from handlers.buttons import button_callback
from handlers.cache_listener import cache_listener
from services.cache_db import db_init
from config import PYRO_API_ID, PYRO_API_HASH, PYRO_SESSION
from pyrogram import Client as PyroClient

URL_FILTER = (filters.Entity(MessageEntity.URL) | filters.CaptionEntity(MessageEntity.URL))

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.exception("Unhandled exception in handler", exc_info=context.error)
    
async def on_startup(app_):
    db_init()
    me = await app_.bot.get_me()
    await set_bot_identity(me.username, me.id)  # <- ключевое, чтобы userbot слал в DM боту
    logging.info(f"[BOT] Я @{me.username} (id={me.id})")

    # мягкий старт Pyrogram (userbot). Если ENV не заданы — просто лог.
    try:
        await get_pyro_app()
    except Exception as e:
        logging.warning(f"[PYRO] skip start: {e}")

async def on_shutdown(app_):
    from state import close_pyro_app
    await close_pyro_app()

def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.User(OWNER_ID) & build_media_filter(), send_file_ids))
    app.add_handler(CommandHandler("id", id_cmd, filters=filters.User(OWNER_ID)))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & URL_FILTER, handle_message))
    app.add_handler(MessageHandler((filters.ChatType.GROUP | filters.ChatType.SUPERGROUP) & URL_FILTER, handle_message))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.Chat(chat_id=int(__import__('config').CACHE_CHAT_ID)) & filters.VIDEO, cache_listener))
    app.add_error_handler(on_error)
    app.post_init = on_startup
    app.post_shutdown = on_shutdown
    logging.info("[БОТ] Готов к работе")
    app.run_polling()


if __name__ == "__main__":
    main()