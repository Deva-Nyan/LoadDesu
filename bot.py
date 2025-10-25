"""Entry point for the cleaned up Telegram bot."""

from __future__ import annotations

import logging

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import logging_setup  # noqa: F401 - configures logging on import
from config import TOKEN
from core.handlers import handle_url_message, send_file_ids, start_command


# What: Log unexpected errors raised by handlers instead of silently swallowing them.
# Inputs: ``update``/``context`` parameters supplied by python-telegram-bot.
# Outputs: None; writes the exception to the configured logger.
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.exception("Unhandled exception in handler", exc_info=context.error)


# What: Build and start the Telegram polling application.
# Inputs: None; uses environment variables for configuration.
# Outputs: None; blocks the process until the bot is stopped.
def main() -> None:
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN is not configured")

    application = ApplicationBuilder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.ALL, send_file_ids), group=0)
    application.add_handler(MessageHandler(filters.ALL, handle_url_message), group=1)
    application.add_error_handler(on_error)

    logging.info("Bot is ready to receive updates")
    application.run_polling()


if __name__ == "__main__":
    main()
