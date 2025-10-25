from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from services.content_key import probe_formats

def build_full_format_keyboard(task_id: str, url: str) -> InlineKeyboardMarkup:
    data = probe_formats(url)
    btns = []
    if data["progressive"]:
        for f in data["progressive"]:
            btns.append([InlineKeyboardButton(f"▶️ {f['label']}", callback_data=f"fmt|{task_id}|{f['fmt']}")])
    if data["merged"]:
        btns.append([InlineKeyboardButton("— склеенные варианты —", callback_data=f"noop|{task_id}")])
        for f in data["merged"]:
            btns.append([InlineKeyboardButton(f"🧩 {f['label']}", callback_data=f"fmt|{task_id}|{f['fmt']}")])
    if data["video_only"]:
        btns.append([InlineKeyboardButton("— видео без звука —", callback_data=f"noop|{task_id}")])
        for f in data["video_only"]:
            btns.append([InlineKeyboardButton(f"🔇 {f['label']}", callback_data=f"fmt|{task_id}|{f['fmt']}")])
    btns.append([InlineKeyboardButton("— аудио —", callback_data=f"noop|{task_id}")])
    btns.append([InlineKeyboardButton("🎵 Audio (mp3)", callback_data=f"aud|{task_id}|mp3")])
    btns.append([InlineKeyboardButton("🎵 Audio (m4a)", callback_data=f"aud|{task_id}|m4a")])
    for f in data["audio_only"][:5]:
        btns.append([InlineKeyboardButton(f"🎵 {f['label']}", callback_data=f"audfmt|{task_id}|{f['fmt']}")])
    btns.append([InlineKeyboardButton("GIF (оптим., ≤50MB)", callback_data=f"gif|{task_id}")])
    if len(btns) == 1:
        btns.insert(0, [InlineKeyboardButton("best (автовыбор)", callback_data=f"fmt|{task_id}|bv*+ba/b")])
    return InlineKeyboardMarkup(btns)