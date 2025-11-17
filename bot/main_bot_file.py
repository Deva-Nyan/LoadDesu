# main.py
import os
import logging
from datetime import datetime
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    InlineQueryHandler,
    CallbackQueryHandler,
    filters,
)

# Импорты из наших модулей
import config
from database import db_init
from handlers import (
    start,
    send_file_ids,
    cache_listener,
    handle_message,
)
from inline_handlers import inline_query
from callback_handlers import button_callback
from userbot import init_userbot, stop_userbot


def setup_logging():
    """Настройка логирования"""
    log_dir = f"logs_{datetime.now():%Y_%m_%d_%H_%M_%S}"
    os.makedirs(log_dir, exist_ok=True)
    
    log_file = os.path.join(log_dir, "log.txt")
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )
    
    # Снижаем уровень логирования для библиотек
    for lib in ("httpx", "telegram", "telegram.ext"):
        logging.getLogger(lib).setLevel(logging.WARNING)
    
    logging.info(f"[БОТ] Запущен, логи в {log_file}")
    
    if not config.PYRO_API_ID or not config.PYRO_API_HASH:
        logging.warning("[PYRO] PYRO_API_ID/PYRO_API_HASH не заданы — отправка через юзербота для >50MB не будет работать.")


def main():
    """Главная функция запуска бота"""
    setup_logging()
    
    app = ApplicationBuilder().token(config.TOKEN).build()

    # Регистрация хендлеров
    app.add_handler(CommandHandler("start", start))
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.User(config.OWNER_ID) & filters.ATTACHMENT,
            send_file_ids,
        )
    )
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(
        MessageHandler(
            filters.Chat(chat_id=config.CACHE_CHAT_ID) & filters.VIDEO,
            cache_listener
        )
    )

    # Хуки для инициализации/завершения
    async def on_startup(_app):
        """Инициализация при старте"""
        db_init()
        
        # Узнаём username/id бота
        me_bot = await _app.bot.get_me()
        config.BOT_USERNAME = me_bot.username
        config.BOT_ID = me_bot.id
        logging.info(f"[BOT] Я @{config.BOT_USERNAME} (id={config.BOT_ID})")
        
        # Стартуем юзербота
        await init_userbot()

    async def on_shutdown(_app):
        """Завершение работы"""
        await stop_userbot()

    app.post_init = on_startup
    app.post_shutdown = on_shutdown

    logging.info("[БОТ] Готов к работе")
    app.run_polling()


if __name__ == "__main__":
    main()
