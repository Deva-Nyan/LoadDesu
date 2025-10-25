from telegram import Update
from telegram.ext import ContextTypes
from config import OWNER_ID


async def send_file_ids(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != OWNER_ID:
        return
    msg = update.effective_message
    parts = []
    def add(label, obj):
        if obj:
            parts.append(f"{label} file_id:\n{obj.file_id}\n{label} unique_id:\n{obj.file_unique_id}")
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
