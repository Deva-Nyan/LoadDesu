#handlers/commands.py
from telegram import Update
from telegram.ext import ContextTypes
from handlers.files_id import send_file_ids

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Используй инлайн @бота или пришли ссылку")


async def id_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg.reply_to_message:
        await msg.reply_text("Ответь этой командой на сообщение с файлом/медиа.")
        return
    fake_update = Update(update.update_id, message=msg.reply_to_message)
    await send_file_ids(fake_update, context)