#bot.py
import re, hashlib, json, subprocess, os, logging, asyncio
from uuid import uuid4
from datetime import datetime
from pathlib import Path
from tempfile import gettempdir
from typing import Optional

from telegram import (
    Update,
    InlineQueryResultCachedPhoto,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputMediaVideo,
    InputMediaAudio,
    InputMediaAnimation,
    InputFile,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    InlineQueryHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ChatType
from telegram.error import BadRequest
import sqlite3
from typing import Optional, Tuple, Dict, Any
from urllib.parse import urlparse

# Pyrogram (userbot)
from pyrogram import Client as PyroClient
from pyrogram.errors import FloodWait, RPCError

# === Конфигурация ===
TOKEN = ""
SAVE_DIR = "/opt/mybot/video"
DB_PATH = os.path.join(SAVE_DIR, "cache.db")
db_conn: Optional[sqlite3.Connection] = None
PLACEHOLDER_PHOTO_ID = ""
MAX_TG_SIZE = 50 * 1024 * 1024  # 50 MB
DOWNLOAD_TASKS: dict[str, str] = {}
OWNER_ID =   # твой Telegram user_id

# Pyrogram конфиг из ENV (или значения по умолчанию)
PYRO_API_ID = int(os.getenv("PYRO_API_ID", "0"))
PYRO_API_HASH = os.getenv("PYRO_API_HASH", "")
PYRO_SESSION = os.getenv("PYRO_SESSION", "userbot_session")  # имя локальной сессии для хранения auth
CACHE_CHAT_ID = int(os.getenv("CACHE_CHAT_ID", ""))
CACHE_THREAD_ID = int(os.getenv("CACHE_THREAD_ID", ""))
BOT_USERNAME: Optional[str] = "LoadDesuRobot"  # без @
BOT_ID: Optional[int] = 8150320476
DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
# cookies: либо явный файл, либо —cookies-from-browser
COOKIES_FILE = os.getenv("COOKIES_FILE", "")
COOKIES_FROM_BROWSER = os.getenv("COOKIES_FROM_BROWSER", "")  # например: "chrome", "firefox"

# разумный дефолт, если просто положишь файл по пути ниже
if not COOKIES_FILE and os.path.exists("/opt/mybot/video/cookies.txt"):
    COOKIES_FILE = "/opt/mybot/video/cookies.txt"

# === Профили форматов (fallback) ===
SMART_FMT = "bv*[height<=1080]+ba/b[height<=1080]/b"
SMART_FMT_1080 = "bv*[height<=1080]+ba/b[height<=1080]/b"
GIF_FMT   = "bv*[height<=480]+ba/b[height<=480]/b"

# Глобальные объекты
pyro_app: Optional[PyroClient] = None
AWAITING_FILES: dict[str, asyncio.Future] = {}  # unique_id -> Future[(file_id, duration, width, height)]

# Создаем директории
os.makedirs(SAVE_DIR, exist_ok=True)
log_dir = f"logs_{datetime.now():%Y_%m_%d_%H_%M_%S}"
os.makedirs(log_dir, exist_ok=True)

# Настройка логов
log_file = os.path.join(log_dir, "log.txt")
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(log_file, encoding="utf-8"),
    ],
)
for lib in ("httpx", "telegram", "telegram.ext"):
    logging.getLogger(lib).setLevel(logging.WARNING)
logging.info(f"[БОТ] Запущен, логи в {log_file}")
if not PYRO_API_ID or not PYRO_API_HASH:
    logging.warning("[PYRO] PYRO_API_ID/PYRO_API_HASH не заданы — отправка через юзербот для >50MB не будет работать.")

# Ограничим одновременные "тяжёлые" задачи (yt-dlp/ffmpeg)
DL_SEM = asyncio.Semaphore(int(os.getenv("MAX_PARALLEL", "2")))

async def run_io(func, *args, **kwargs):
    """Выполняет синхронную функцию в отдельном потоке, не блокируя event-loop."""
    return await asyncio.to_thread(func, *args, **kwargs)

# --- хэндлер: прислали медиа -> вернуть file_id ---
async def send_file_ids(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # пускаем только тебя
    if not update.effective_user or update.effective_user.id != OWNER_ID:
        return

    msg = update.effective_message
    parts = []

    def add(label, obj):
        if obj:
            parts.append(
                f"{label} file_id:\n{obj.file_id}\n{label} unique_id:\n{obj.file_unique_id}"
            )

    # фото приходит массивом размеров — берём самый большой
    if msg.photo:
        add("photo", msg.photo[-1])

    # остальное — одиночные объекты
    add("document", msg.document)
    add("animation", msg.animation)
    add("video", msg.video)
    add("sticker", msg.sticker)
    add("audio", msg.audio)
    add("voice", msg.voice)
    add("video_note", msg.video_note)

    if not parts:
        # ничего медийного — молчим
        return

    text = "⚙️ Нашёл ID:\n\n" + "\n\n".join(parts)

    # ответим реплаем и продублируем в логи
    await msg.reply_text(text)
    print(text)

# === Утилиты ===
_YT_ID = re.compile(r'(?:v=|/shorts/|youtu\.be/)([A-Za-z0-9_-]{11})')

def normalize_youtube_url(url: str) -> str:
    if "youtu" in url:
        m = _YT_ID.search(url)
        if m:
            return f"https://www.youtube.com/watch?v={m.group(1)}"
    return url

def extract_youtube_id(url: str) -> Optional[str]:
    m = _YT_ID.search(url)
    return m.group(1) if m else None

def format_bytes(n: int) -> str:
    x = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if x < 1024 or unit == "GB":
            return f"{x:.0f} {unit}" if unit == "B" else f"{x:.2f} {unit}"
        x /= 1024

def _pick_single_path(stdout: str) -> str:
    lines = [ln.strip() for ln in stdout.splitlines() if ln.strip()]
    if not lines:
        raise RuntimeError("yt-dlp did not print any file path")
    if len(lines) > 1:
        logging.warning(f"[YTDLP] multiple outputs detected ({len(lines)}). Using the last one:\n" +
                        "\n".join(lines))
    return lines[-1]

def _origin(url: str) -> str:
    u = urlparse(url)
    return f"{u.scheme}://{u.netloc}/"

def get_video_info(video_path: str):
    """Определяет длительность, ширину и высоту видео."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        lines = result.stdout.strip().split("\n")
        width, height, duration = int(lines[0]), int(lines[1]), float(lines[2])
        return int(duration), width, height
    except Exception as e:
        logging.warning(f"⚠ Не удалось получить параметры видео: {e}")
        return 0, 640, 360  # fallback

def download_animation_source(url: str) -> str:
    """Скачиваем умеренный исходник под анимацию (низкая высота, mp4)."""
    cmd = [
        "yt-dlp", "-f", GIF_FMT,
        "--no-playlist", "--no-simulate",
        "--print", "after_move:filepath",
        "-o", os.path.join(SAVE_DIR, "%(title)s [%(id)s].%(ext)s"),
        url,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return r.stdout.strip()

def video_to_tg_animation(in_path: str, target_mb: int = 50) -> str:
    """
    Делает тихий MP4 для sendAnimation (без звука).
    Пытаемся уложиться в target_mb, уменьшая ширину/CRF/FPS.
    """
    base, _ = os.path.splitext(in_path)
    out = base + ".anim.mp4"

    attempts = [
        (480, 30, 23),
        (360, 30, 24),
        (320, 24, 26),
    ]
    for w, fps, crf in attempts:
        # перекодируем в H.264, без аудио, yuv420p
        subprocess.run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", in_path,
            "-an",
            "-vf", f"scale=min({w}\\,iw):-2:flags=lanczos,fps={fps}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "baseline",
            "-movflags", "+faststart",
            "-crf", str(crf),
            out,
        ], check=True)
        if os.path.getsize(out) <= target_mb * 1024 * 1024:
            break
    return out

def generate_thumbnail(video_path: str) -> Optional[str]:
    """
    Делает JPEG-превью ≤320px, ≤200KB.
    Пытается взять кадр на 2s, потом 0.5s, потом 0s.
    Возвращает путь или None.
    """
    out_path = Path(video_path).with_suffix(".thumb.jpg")  # безопаснее, чем stem/_thumb
    tries = ["00:00:02", "00:00:00.5", "00:00:00"]

    for ss in tries:
        try:
            # Берём 1 кадр. Важно: без одинарных кавычек в -vf (мы не через shell)
            subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-ss", ss, "-i", video_path,
                "-frames:v", "1",
                "-vf", "scale=min(320\\,iw):min(320\\,ih):force_original_aspect_ratio=decrease",
                "-q:v", "5",
                str(out_path),
            ], check=True, capture_output=True)

            if os.path.exists(out_path):
                # сжать, если >200KB
                if os.path.getsize(out_path) > 200 * 1024:
                    subprocess.run([
                        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-i", str(out_path),
                        "-vf", "scale=min(320\\,iw):min(320\\,ih):force_original_aspect_ratio=decrease",
                        "-q:v", "10",
                        str(out_path),
                    ], check=True, capture_output=True)

                size_kb = os.path.getsize(out_path) // 1024
                logging.info(f"[THUMBNAIL] Создано превью: {out_path} ({size_kb} KB)")
                return str(out_path)
        except Exception as e:
            logging.info(f"[THUMBNAIL] Попытка ss={ss} не удалась: {e}")

    logging.warning("[THUMBNAIL] Не удалось создать превью — продолжаем без него")
    return None

# === Форматы и GIF ===
def probe_formats(url: str):
    try:
        r = subprocess.run(["yt-dlp", "-J", url], capture_output=True, text=True, check=True)
        info = json.loads(r.stdout)
        fmts = info.get("formats", []) or []

        def human_size(x):
            s = x or 0
            if s <= 0: return ""
            for u in ("B","KB","MB","GB"):
                if s < 1024 or u == "GB":
                    return f"{s:.0f} {u}" if u == "B" else f"{s/1024:.2f} {u}"
                s /= 1024

        best_m4a = best_audio = None
        for f in fmts:
            if f.get("acodec") != "none" and f.get("vcodec") == "none":
                if (f.get("ext") == "m4a") and (not best_m4a or (f.get("tbr") or 0) > (best_m4a.get("tbr") or 0)):
                    best_m4a = f
                if not best_audio or (f.get("tbr") or 0) > (best_audio.get("tbr") or 0):
                    best_audio = f

        progressive, merged, video_only, audio_only = [], [], [], []
        for f in fmts:
            v, a = f.get("vcodec"), f.get("acodec")
            itag = str(f.get("format_id"))
            ext  = f.get("ext")
            h    = f.get("height")
            fps  = f.get("fps")
            kbps = f.get("tbr")
            sz   = f.get("filesize") or f.get("filesize_approx")

            def lbl(prefix=""):
                L = []
                if h: L.append(f"{h}p")
                if ext: L.append(ext)
                if fps: L.append(f"{int(fps)}fps")
                if kbps: L.append(f"~{int(kbps)}kbps")
                S = " ".join(L)
                if sz: S += f" ({human_size(sz)})"
                return (prefix + " " + S).strip()

            if v != "none" and a != "none":
                progressive.append({"fmt": itag, "label": lbl()})
            elif v != "none":
                video_only.append({"fmt": itag, "label": lbl()})
                aud = best_m4a or best_audio
                if aud:
                    merged.append({"fmt": f"{itag}+{aud['format_id']}", "label": lbl("+ audio")})
            elif a != "none":
                a_lbl = f"{ext} ~{int(kbps)}kbps" if kbps else ext or "audio"
                if sz: a_lbl += f" ({human_size(sz)})"
                audio_only.append({"fmt": itag, "label": a_lbl})

        def p_h(x):
            try: return int(x["label"].split("p")[0])
            except: return 0

        progressive.sort(key=p_h, reverse=True)
        merged.sort(key=p_h, reverse=True)
        video_only.sort(key=p_h, reverse=True)
        audio_only.sort(key=lambda x: int(x["label"].split("~")[-1].split("kbps")[0]) if "~" in x["label"] else 0, reverse=True)

        logging.info(f"[KB/PROBE] prog={len(progressive)} merged={len(merged)} vonly={len(video_only)} aonly={len(audio_only)} для {url}")
        return {"progressive": progressive[:10], "merged": merged[:10], "video_only": video_only[:10], "audio_only": audio_only[:8]}
    except Exception as e:
        logging.warning(f"[FMT] Не удалось получить список форматов: {e}")
        return {"progressive": [], "merged": [], "video_only": [], "audio_only": []}

def build_full_format_keyboard(task_id: str, url: str):
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

    # аудио
    btns.append([InlineKeyboardButton("— аудио —", callback_data=f"noop|{task_id}")])
    btns.append([InlineKeyboardButton("🎵 Audio (mp3)", callback_data=f"aud|{task_id}|mp3")])
    btns.append([InlineKeyboardButton("🎵 Audio (m4a)", callback_data=f"aud|{task_id}|m4a")])
    for f in data["audio_only"][:5]:
        btns.append([InlineKeyboardButton(f"🎵 {f['label']}", callback_data=f"audfmt|{task_id}|{f['fmt']}")])

    btns.append([InlineKeyboardButton("GIF (оптим., ≤50MB)", callback_data=f"gif|{task_id}")])

    logging.info(f"[KB/BUILD] full-kb для {url}: rows={len(btns)}")
    if len(btns) == 1:
        btns.insert(0, [InlineKeyboardButton("best (автовыбор)", callback_data=f"fmt|{task_id}|bv*+ba/b")])
    return InlineKeyboardMarkup(btns)

def download_video_with_format(url: str, fmt_id: str) -> str:
    """Скачивает видео с указанным форматом и приводит к mp4."""
    logging.info(f"[DL] Пользователь выбрал формат: {fmt_id}")
    cmd = [
        "yt-dlp",
        "-f", fmt_id,
        "--recode-video", "mp4",
        "--no-simulate", "--restrict-filenames",
        "--print", "after_move:filepath",
        "-o", os.path.join(SAVE_DIR, "%(title)s [%(id)s].%(ext)s"),
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    path = result.stdout.strip()
    try:
        size = os.path.getsize(path)
        logging.info(f"[DL] Файл скачан: {path} ({format_bytes(size)})")
    except Exception as e:
        logging.warning(f"[DL] Не удалось получить размер: {e}")
    return path

def video_to_gif(in_path: str) -> str:
    """Конвертирует mp4 в оптимизированную GIF ≤50MB (по возможности)."""
    base = os.path.splitext(in_path)[0]
    palette = base + "_palette.png"
    out = base + ".gif"

    def make_gif(scale_w: int, fps: int):
        vf = f"fps={fps},scale={scale_w}:-1:flags=lanczos"
        # палитра
        subprocess.run([
            "ffmpeg", "-y", "-i", in_path,
            "-vf", f"{vf},palettegen",
            palette
        ], check=True, capture_output=True)
        # применение палитры
        subprocess.run([
            "ffmpeg", "-y", "-i", in_path, "-i", palette,
            "-filter_complex", f"{vf}[x];[x][1:v]paletteuse=dither=sierra2_4a",
            "-loop", "0",
            out
        ], check=True, capture_output=True)
        if os.path.exists(palette):
            os.remove(palette)

    # 1-я попытка: 480px, 12fps
    try_order = [(480, 12), (360, 10), (320, 8)]
    for w, fps in try_order:
        make_gif(w, fps)
        sz = os.path.getsize(out)
        logging.info(f"[GIF] {out} = {format_bytes(sz)} (целевой лимит {format_bytes(MAX_TG_SIZE)})")
        if sz <= MAX_TG_SIZE:
            return out
        else:
            try:
                os.remove(out)
            except Exception:
                pass

    # не уложились — делаем последнюю максимально облегченную версию и всё равно вернём
    make_gif(320, 6)
    return out

def download_audio(url: str, fmt: str = "mp3") -> str:
    base = [
        "yt-dlp",
        "-x", "--audio-format", fmt, "--audio-quality", "0",
        "--no-playlist", "--no-simulate", "--restrict-filenames",
        "--print", "after_move:filepath",
        "-o", os.path.join(SAVE_DIR, "%(title)s [%(id)s].%(ext)s"),
        url,
    ]
    cmd = (["yt-dlp", "-f", "bestaudio[ext=m4a]/bestaudio"] + base[1:]) if fmt == "m4a" else base
    r = subprocess.run(cmd, capture_output=True, text=True, check=True)
    path = r.stdout.strip()
    logging.info(f"[AUDIO] готово: {path}")
    return path

def download_gif_from_url(url: str) -> str:
    """Скачивает ролик с умеренным качеством и конвертит в GIF."""
    fmt = GIF_FMT
    logging.info(f"[GIF] Скачиваем источник под GIF: -f \"{fmt}\"")
    cmd = [
        "yt-dlp", "-f", fmt,
        "--no-playlist", "--no-simulate", "--restrict-filenames",
        "--print", "after_move:filepath",
        "-o", os.path.join(SAVE_DIR, "%(title)s [%(id)s].%(ext)s"),
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    mp4_path = result.stdout.strip()
    return video_to_gif(mp4_path)

def download_video_smart(url: str, fmt: str = SMART_FMT_1080) -> str:
    """Скачивает видео (MP4). При ошибке пробует ретрай с Referer/UA/cookies."""
    logging.info(f"[SMART] -f \"{fmt}\"")

    base_cmd = [
        "yt-dlp",
        "-f", fmt,
        "--recode-video", "mp4",
        "--no-playlist",
        "--no-simulate",
        "--print", "after_move:filepath",
        "-o", os.path.join(SAVE_DIR, "%(title)s [%(id)s].%(ext)s"),
        url,
    ]
    try:
        r = subprocess.run(base_cmd, capture_output=True, text=True, check=True)
        path = _pick_single_path(r.stdout)
        logging.info(f"[SMART] saved {path} ({format_bytes(os.path.getsize(path))})")
        return path
    except subprocess.CalledProcessError as e:
        err = e.stderr if isinstance(e.stderr, str) else (e.stderr.decode() if e.stderr else str(e))
        logging.error(f"[SMART] primary yt-dlp failed:\n{err}")

        # Ретрай: generic best + Referer/UA (+ cookies если заданы)
        cmd = [
            "yt-dlp", "-f", "best",
            "--no-playlist", "--max-downloads", "1",
            "--no-simulate",
            "--add-header", f"Referer: {_origin(url)}",
            "--user-agent", DEFAULT_UA,
            "--print", "after_move:filepath",
            "-o", os.path.join(SAVE_DIR, "%(title)s [%(id)s].%(ext)s"),
            url,
        ]
        if COOKIES_FILE:
            cmd += ["--cookies", COOKIES_FILE]
        elif COOKIES_FROM_BROWSER:
            cmd += ["--cookies-from-browser", COOKIES_FROM_BROWSER]

        logging.info("[SMART] retry with Referer/UA" + (" + cookies" if (COOKIES_FILE or COOKIES_FROM_BROWSER) else ""))
        r2 = subprocess.run(cmd, capture_output=True, text=True, check=True)
        path = _pick_single_path(r2.stdout)
        logging.info(f"[SMART] retry saved {path} ({format_bytes(os.path.getsize(path))})")
        return path

def compress_video(path: str) -> str:
    """Сжимает видео через ffmpeg под лимит MAX_TG_SIZE (двухпроходное)."""
    size = os.path.getsize(path)
    if size <= MAX_TG_SIZE:
        logging.info(f"[COMPRESSION] Уже в лимите: {format_bytes(size)} ≤ {format_bytes(MAX_TG_SIZE)}")
        return path

    duration, src_w, src_h = get_video_info(path)
    logging.info(f"[COMPRESSION] Исходник: {format_bytes(size)}, duration={duration:.2f}s, {src_w}x{src_h}")

    if duration <= 0:
        target_total_kbps = 950  # запасной вариант
    else:
        target_bits = int(MAX_TG_SIZE * 0.96 * 8)  # небольшой запас
        target_total_kbps = max(384, target_bits // max(1, int(duration)) // 1000)

    audio_kbps = 128
    video_kbps = max(300, target_total_kbps - audio_kbps)

    scale_vf = "scale=-2:720"
    base, _ = os.path.splitext(path)
    out = f"{base}_compressed.mp4"

    logging.info(
        f"[COMPRESSION] Цель: ~{format_bytes(MAX_TG_SIZE)} | total≈{target_total_kbps}kbps "
        f"(video≈{video_kbps}kbps, audio={audio_kbps}kbps), vf='{scale_vf}'"
    )

    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", path,
            "-vf", scale_vf,
            "-c:v", "libx264", "-b:v", f"{video_kbps}k",
            "-pass", "1", "-preset", "veryfast", "-tune", "fastdecode",
            "-an", "-f", "mp4", os.devnull,
        ], check=True, capture_output=True)

        subprocess.run([
            "ffmpeg", "-y", "-i", path,
            "-vf", scale_vf,
            "-c:v", "libx264", "-b:v", f"{video_kbps}k",
            "-pass", "2", "-preset", "veryfast", "-tune", "fastdecode",
            "-c:a", "aac", "-b:a", f"{audio_kbps}k",
            out,
        ], check=True, capture_output=True)
    finally:
        for f in ("ffmpeg2pass-0.log", "ffmpeg2pass-0.log.mbtree"):
            if os.path.exists(f):
                os.remove(f)

    new_size = os.path.getsize(out)
    logging.info(f"[COMPRESSION] Результат: {format_bytes(new_size)}")

    if new_size > MAX_TG_SIZE:
        logging.warning("[COMPRESSION] Всё ещё больше лимита — оставляем оригинал")
        try:
            os.remove(out)
        except Exception:
            pass
        return path

    return out

async def send_via_userbot(video_path: str, caption: Optional[str] = None, bot=None):
    if not pyro_app:
        raise RuntimeError("Pyrogram не запущен или не сконфигурирован.")
    if bot is None:
        raise RuntimeError("Нужно передать bot (context.bot).")
    
    duration, width, height = await run_io(get_video_info, video_path)
    thumb = await run_io(generate_thumbnail, video_path)
    
    base_kwargs = dict(
        caption=caption or "",
        supports_streaming=True,
        width=width, height=height, duration=duration,
    )
    if thumb and os.path.exists(thumb):
        base_kwargs["thumb"] = thumb

    # 1) юзербот отправляет в ЛС боту (чтобы всё равно было в истории ЛС)
    dm_chat = f"@{BOT_USERNAME}" if BOT_USERNAME else BOT_ID
    try:
        await pyro_app.send_message(dm_chat, "/start")
        logging.info("[PYRO→DM] /start отправлен боту")
    except Exception as e:
        logging.info(f"[PYRO→DM] /start: {e}")
    msg_dm = await pyro_app.send_video(chat_id=dm_chat, video=video_path, **base_kwargs)
    logging.info(f"[PYRO→DM] Видео отправлено. unique_id={msg_dm.video.file_unique_id}")

    # 2) юзербот дублирует в КЭШ-ЧАТ и запоминаем message_id из КЭШ-ЧАТА
    msg_cache = await pyro_app.send_video(chat_id=CACHE_CHAT_ID, video=video_path, **base_kwargs)
    logging.info(f"[PYRO→CACHE] Дубликат отправлен. message_id={msg_cache.id}")

    # 3) бот копирует ИМЕННО из КЭШ-ЧАТА (тут message_id совпадают для всех)
    copied = await bot.forward_message(
        chat_id=CACHE_CHAT_ID,
        from_chat_id=CACHE_CHAT_ID,
        message_id=msg_cache.id
    )
    v = copied.video
    bot_file_id = v.file_id

    logging.info(f"[BOT] Получен file_id: {bot_file_id}")

    try:
        if thumb and os.path.exists(thumb):
            os.remove(thumb)
    except Exception:
        pass

    return bot_file_id, (v.duration or duration or 0), (v.width or width or 0), (v.height or height or 0)

# === Хендлер, который ловит видео в кэш-чате и резолвит file_id ===
async def cache_listener(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.video:
        return

    v = msg.video
    unique = v.file_unique_id
    from_chat = update.effective_chat
    chat_info = f"{from_chat.type} {from_chat.id} ({getattr(from_chat, 'username', '')})"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if unique in AWAITING_FILES:
        logging.info(f"[CACHE] {ts} — Видео получено. chat={chat_info} unique_id={unique} file_id={v.file_id}")
        fut = AWAITING_FILES.get(unique)
        if fut and not fut.done():
            fut.set_result((v.file_id, v.duration or 0, v.width or 0, v.height or 0))
    else:
        logging.info(f"[CACHE] {ts} — Видео не запрашивалось. chat={chat_info} unique_id={unique} file_id={v.file_id}")

# === PTB-хендлеры ===
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    chat_type = update.message.chat.type

    if chat_type in ("group", "supergroup"):
        bot_username = context.bot.username
        if f"@{bot_username}" not in text:
            return
        text = text.replace(f"@{bot_username}", "").strip()

    url = text
    logging.info(f"[БОТ] Ссылка: {url}")
    status = await update.message.reply_text("Скачиваю...")

    video_path = None
    try:
        async with DL_SEM:
            video_path = await run_io(download_video_smart, url)

        size = os.path.getsize(video_path)
        logging.info(f"[SEND] Итоговый файл {format_bytes(size)} (лимит {format_bytes(MAX_TG_SIZE)})")

        if size > MAX_TG_SIZE:
            logging.info("[SEND] >50MB — отправляем через юзербота")
            file_id, duration, width, height = await send_via_userbot(video_path, caption=f"Кэширование… {url}", bot=context.bot)
            await status.edit_text("Готово!")
            await update.message.reply_video(video=file_id, caption=f"Видео готово: {url}")
        else:
            await status.edit_text("Готово!")
            await update.message.reply_video(video=open(video_path, "rb"), caption=f"Видео готово: {url}")

    except subprocess.CalledProcessError as e:
        err = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or str(e))
        logging.error(f"[БОТ] Ошибка subprocess: {err}")

        # Если формат недоступен — предложим выбор формата/GIF
        if "Requested format is not available" in err or "--list-formats" in err:
            task = uuid4().hex[:8]
            DOWNLOAD_TASKS[task] = url
            kb = build_full_format_keyboard(task, url)
            await status.edit_text("Этот формат недоступен.\nВыбери другой формат или GIF:", reply_markup=kb)
        else:
            await status.edit_text(f"Ошибка: {err}")

    except Exception as e:
        logging.error(f"[БОТ] Ошибка: {e}")
        await status.edit_text(f"Ошибка: {e}")
    finally:
        try:
            if video_path and os.path.exists(video_path):
                os.remove(video_path)
        except Exception:
            pass

from telegram.error import BadRequest

async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.inline_query.query.strip()
    if not url.startswith("http"):
        return

    task = uuid4().hex[:8]
    DOWNLOAD_TASKS[task] = url

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Автовыбор", callback_data=f"auto|{task}"),
         InlineKeyboardButton("🎬 Видео",     callback_data=f"vauto|{task}")],
        [InlineKeyboardButton("🎵 Аудио",     callback_data=f"aauto|{task}"),
         InlineKeyboardButton("➕ Больше",     callback_data=f"more|{task}")],
    ])

    result = InlineQueryResultCachedPhoto(
        id=task,
        photo_file_id=PLACEHOLDER_PHOTO_ID,
        caption=f"Ссылка: {url}",
        reply_markup=kb,                            # <-- вот тут используем kb
    )
    try:
        await update.inline_query.answer([result], cache_time=0, is_personal=True)
        logging.info(f"[INLINE] task={task} show mini-menu for {url}")
    except BadRequest as e:
        # если плейсхолдер невалиден — фоллбек на Article, но с той же клавой
        from telegram import InlineQueryResultArticle, InputTextMessageContent
        fallback = InlineQueryResultArticle(
            id=task,
            title="Скачать",
            input_message_content=InputTextMessageContent(f"Видео готовится: {url}"),
            reply_markup=kb,
        )
        await update.inline_query.answer([fallback], cache_time=0, is_personal=True)
        logging.info(f"[INLINE] task={task} fallback Article (placeholder invalid)")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    parts = data.split("|")
    action = parts[0]                  # dl | fmt | gif
    task_id = parts[1]
    url = DOWNLOAD_TASKS.get(task_id)
    inline_id = query.inline_message_id

    if not url:
        await query.edit_message_caption(caption="Ошибка: ссылка устарела или не найдена.")
        return
    
    logging.info(f"[BTN] action={action} task={task_id} url={url}")

    # Обновим подпись (для любого action, кроме служебных случаев)
    async def _set_caption(text: str, kb=None):
        try:
            if query.message:
                await context.bot.edit_message_caption(
                    chat_id=query.message.chat_id,
                    message_id=query.message.message_id,
                    caption=text,
                    reply_markup=kb
                )
            else:
                await context.bot.edit_message_caption(
                    inline_message_id=inline_id,
                    caption=text,
                    reply_markup=kb
                )
            logging.info(f"[BTN] caption set: {text[:80]}")
        except BadRequest as e:
            if "message is not modified" in str(e).lower():
                logging.info("[BTN] caption unchanged (noop)")
            else:
                logging.error(f"[BTN] edit_message_caption error: {e}")

    # === ВЕТКА: пользователь нажал 'Скачать' (dl) ===
    if action == "dl":
        kb = build_full_format_keyboard(task_id, url)
        await _set_caption(f"Выбери формат для: {url}", kb)
        return

    # необязательный разделитель из клавиатуры
    if action == "noop":
        return

    # === ВЕТКА: пользователь выбрал конкретный формат (fmt|task|fmt_id) ===
    if action == "fmt":
        fmt_id = parts[2]  # может быть '18' или '137+140' и т.п.
        content_key, title = get_content_key_and_title(url)
        variant = f"video:fmt={fmt_id}"

        # 0) кеш-хит?
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
            # 1) качаем выбранный формат (поддерживает и 'video+audio')
            async with DL_SEM:
                video_path = await run_io(download_video_with_format, url, fmt_id)
            size = os.path.getsize(video_path)
            logging.info(f"[FMT] {fmt_id} → {format_bytes(size)}: {video_path}")

        except subprocess.CalledProcessError as e:
            err = e.stderr if isinstance(e.stderr, str) else (e.stderr.decode() if e.stderr else str(e))
            logging.error(f"[FMT] yt-dlp error for {fmt_id}: {err}")
            # 1а) фоллбэк: общий профиль ≤1080p
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

        # 2) отправка и кеш
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

            # 3) сохраняем в кеш
            cache_put(
                content_key, variant, kind="video",
                file_id=file_id, file_unique_id=file_unique_id,
                width=width, height=height, duration=duration, size=size,
                fmt_used=fmt_id, title=title, source_url=url
            )
            logging.info(f"[CACHE SAVE] {content_key} [{variant}]")

            # 4) заменяем плейсхолдер
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
        return

    # === ВЕТКА: GIF/Animation (генерим тихий MP4 под sendAnimation) ===
    if action == "gif":
        content_key, title = get_content_key_and_title(url)
        variant = "anim:50"   # «профиль» — анимация ≤50MB

        # 0) кеш-хит?
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
            # 1) скачиваем умеренный исходник под анимацию
            async with DL_SEM:
                src_path = await run_io(download_animation_source, url)
            logging.info(f"[ANIM] source: {src_path} ({format_bytes(os.path.getsize(src_path))})")

            # 2) конвертим в «тихий» MP4 (H.264 yuv420p, без аудио, ≤50MB)
            async with DL_SEM:
                anim_path = await run_io(video_to_tg_animation, src_path, target_mb=50)
            anim_size = os.path.getsize(anim_path)
            logging.info(f"[ANIM] ready: {anim_path} ({format_bytes(anim_size)})")

            # 3) отправляем, берём file_id и метаданные
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
                # заменяем инлайн-сообщение на кешированный file_id
                await context.bot.edit_message_media(
                    inline_message_id=inline_id,
                    media=InputMediaAnimation(media=file_id, caption=f"GIF готова: {url}")
                )
            else:
                # если это инлайн без query.message (редко), просто меняем подпись
                # (но лучше всё равно отправить в кэш-чат и взять file_id, если нужно)
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

            # 4) кладём в кеш
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
        return
        
    # === ВЕТКА: пользователь нажал "🎵 Audio (mp3|m4a)" ===
    if action == "aud":
        aud_fmt = parts[2].lower()  # 'mp3' или 'm4a'
        content_key, title = get_content_key_and_title(url)
        variant = f"audio:{aud_fmt}"

        # кеш
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
        except subprocess.CalledProcessError as e:
            err = e.stderr if isinstance(e.stderr, str) else (e.stderr.decode() if e.stderr else str(e))
            logging.error(f"[INLINE/AUD] Ошибка аудио ({aud_fmt}): {err}")
            kb = build_full_format_keyboard(task_id, url)
            await _set_caption("Не удалось получить аудио.")
            return
        except Exception as e:
            logging.error(f"[INLINE/AUD] fail: {e}")
            kb = build_full_format_keyboard(task_id, url)
            await _set_caption("Не удалось получить аудио.")
            return
        finally:
            try:
                if audio_path and os.path.exists(audio_path):
                    os.remove(audio_path)
            except Exception:
                pass
        return

    # показать полную клавиатуру
    if action == "more":
        kb = build_full_format_keyboard(task_id, url)
        await _set_caption(f"Все форматы:", kb)
        return

    # спокойный no-op разделитель
    if action == "noop":
        return
        
    # ⚡ Автовыбор: ≤1080p
    # === ВЕТКА: авто ===
    if action == "auto":
        # mode: 'video' | 'audio' | 'unknown'
        mode, content_key, title = detect_media_kind_and_key(url)
        logging.info(f"[AUTO] mode={mode} key={content_key} url={url}")

        # ---------- helper для ответа cached ----------
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

        # ---------- ветка: ВИДЕО ----------
        if mode == "video":
            variant = "video:smart1080"
            row = cache_get(content_key, variant)
            if row:
                logging.info(f"[CACHE HIT] {content_key} [{variant}]")
                await _reply_cached("video", row["file_id"])
                return

            await _set_caption("Скачиваю (автовыбор: видео ≤1080p)…")
            video_path = thumb = None
            try:
                # качаем смарт-склейку ≤1080p
                async with DL_SEM:
                    video_path = await run_io(download_video_smart, url, fmt=SMART_FMT_1080)
                size = os.path.getsize(video_path)
                logging.info(f"[AUTO/VIDEO] downloaded {format_bytes(size)} → {video_path}")

                # отправляем: ботом или через юзербота
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

                # кладём в кэш и показываем пользователю
                cache_put(
                    content_key, variant, kind="video",
                    file_id=file_id, file_unique_id=file_unique_id,
                    width=width, height=height, duration=duration, size=size,
                    fmt_used=SMART_FMT_1080, title=title, source_url=url
                )
                await _reply_cached("video", file_id)

            except Exception as e:
                logging.error(f"[AUTO/VIDEO] fail: {e} — переключаюсь на аудио")
                # fallback: АУДИО
                variant = "audio:mp3"
                row = cache_get(content_key, variant)
                if row:
                    logging.info(f"[CACHE HIT] {content_key} [{variant}]")
                    await _reply_cached("audio", row["file_id"])
                    return
                try:
                    title_full, artist = extract_title_artist(url, title)

                    #audio_path = download_audio(url, fmt="mp3")
                    async with DL_SEM:
                        audio_path = await run_io(download_audio, url, fmt="mp3")
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
                        content_key, variant, kind="audio",
                        file_id=file_id, file_unique_id=sent.audio.file_unique_id,
                        width=None, height=None, duration=None, size=os.path.getsize(audio_path),
                        fmt_used="mp3", title=title, source_url=url
                    )
                    await _reply_cached("audio", file_id)
                except Exception as e2:
                    logging.error(f"[AUTO/FALLBACK-AUDIO] fail: {e2}")
                    kb = build_full_format_keyboard(task_id, url)
                    await _set_caption("Не удалось автовыбрать. Выбери формат:", kb)
                finally:
                    try:
                        if 'audio_path' in locals() and os.path.exists(audio_path): os.remove(audio_path)
                    except: pass
            finally:
                try:
                    if thumb and os.path.exists(thumb): os.remove(thumb)
                    if video_path and os.path.exists(video_path): os.remove(video_path)
                except: pass
            return

        # ---------- ветка: АУДИО ----------
        if mode == "audio":
            variant = "audio:mp3"
            row = cache_get(content_key, variant)
            if row:
                logging.info(f"[CACHE HIT] {content_key} [{variant}]")
                await _reply_cached("audio", row["file_id"])
                return

            await _set_caption("Скачиваю (автовыбор: аудио)…")
            try:
                #audio_path = download_audio(url, fmt="mp3")
                async with DL_SEM:
                    audio_path = await run_io(download_audio, url, fmt="mp3")
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
                    content_key, variant, kind="audio",
                    file_id=file_id, file_unique_id=sent.audio.file_unique_id,
                    width=None, height=None, duration=None, size=os.path.getsize(audio_path),
                    fmt_used="mp3", title=title, source_url=url
                )
                await _reply_cached("audio", file_id)

            except Exception as e:
                logging.error(f"[AUTO/AUDIO] fail: {e} — пробую видео")
                # fallback: ВИДЕО
                variant_v = "video:smart1080"
                row = cache_get(content_key, variant_v)
                if row:
                    logging.info(f"[CACHE HIT] {content_key} [{variant_v}]")
                    await _reply_cached("video", row["file_id"])
                    return
                try:
                    #video_path = download_video_smart(url, fmt=SMART_FMT_1080)
                    async with DL_SEM:
                        video_path = await run_io(download_video_smart, url, fmt=SMART_FMT_1080)
                    size = os.path.getsize(video_path)
                    logging.info(f"[AUTO/FALLBACK-VIDEO] {format_bytes(size)}")

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
                        content_key, variant_v, kind="video",
                        file_id=file_id, file_unique_id=file_unique_id,
                        width=width, height=height, duration=duration, size=size,
                        fmt_used=SMART_FMT_1080, title=title, source_url=url
                    )
                    await _reply_cached("video", file_id)

                except Exception as e2:
                    logging.error(f"[AUTO/FALLBACK-VIDEO] fail: {e2}")
                    kb = build_full_format_keyboard(task_id, url)
                    await _set_caption("Не удалось автовыбрать. Выбери формат:", kb)
                finally:
                    try:
                        if 'thumb' in locals() and thumb and os.path.exists(thumb): os.remove(thumb)
                        if 'video_path' in locals() and video_path and os.path.exists(video_path): os.remove(video_path)
                    except: pass
            finally:
                try:
                    if 'audio_path' in locals() and os.path.exists(audio_path): os.remove(audio_path)
                except: pass
            return

        # ---------- ветка: UNKNOWN (сначала видео, потом аудио, тоже с кешем) ----------
        await _set_caption("Скачиваю (автовыбор)…")
        # 1) попробовать видео
        variant_v = "video:smart1080"
        row = cache_get(content_key, variant_v)
        if row:
            logging.info(f"[CACHE HIT] {content_key} [{variant_v}]")
            await _reply_cached("video", row["file_id"])
            return
        video_path = thumb = None
        try:
            #video_path = download_video_smart(url, fmt=SMART_FMT_1080)
            async with DL_SEM:
                video_path = await run_io(download_video_smart, url, fmt=SMART_FMT_1080)
            size = os.path.getsize(video_path)
            logging.info(f"[AUTO/UNKNOWN→VIDEO] {format_bytes(size)}")
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
                content_key, variant_v, kind="video",
                file_id=file_id, file_unique_id=file_unique_id,
                width=width, height=height, duration=duration, size=size,
                fmt_used=SMART_FMT_1080, title=title, source_url=url
            )
            await _reply_cached("video", file_id)
            return
        except Exception as e:
            logging.error(f"[AUTO/UNKNOWN] video fail: {e} — пробую аудио")
        finally:
            try:
                if thumb and os.path.exists(thumb): os.remove(thumb)
                if video_path and os.path.exists(video_path): os.remove(video_path)
            except: pass

        # 2) аудио
        variant_a = "audio:mp3"
        row = cache_get(content_key, variant_a)
        if row:
            logging.info(f"[CACHE HIT] {content_key} [{variant_a}]")
            await _reply_cached("audio", row["file_id"])
            return
        try:
            #audio_path = download_audio(url, fmt="mp3")
            async with DL_SEM:
                audio_path = await run_io(download_audio, url, fmt="mp3")
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
                content_key, variant_a, kind="audio",
                file_id=file_id, file_unique_id=sent.audio.file_unique_id,
                width=None, height=None, duration=None, size=os.path.getsize(audio_path),
                fmt_used="mp3", title=title, source_url=url
            )
            await _reply_cached("audio", file_id)
        except Exception as e2:
            logging.error(f"[AUTO/UNKNOWN] audio fail: {e2}")
            kb = build_full_format_keyboard(task_id, url)
            await _set_caption("Не удалось автовыбрать. Выбери формат:", kb)
        finally:
            try:
                if 'audio_path' in locals() and os.path.exists(audio_path): os.remove(audio_path)
            except: pass
        return

    # 🎬 Видео: сразу ≤1080p (с кешем)
    if action == "vauto":
        content_key, title = get_content_key_and_title(url)
        variant = "video:smart1080"

        # 1) кеш-хит?
        row = cache_get(content_key, variant)
        if row:
            logging.info(f"[CACHE HIT] {content_key} [{variant}]")
            await context.bot.edit_message_media(
                inline_message_id=inline_id,
                media=InputMediaVideo(media=row["file_id"], caption=f"Видео готово: {url}")
            )
            return

        # 2) качаем
        await _set_caption("Скачиваю видео (≤1080p)…")
        video_path = None
        thumb = None
        try:
            async with DL_SEM:
                video_path = await run_io(download_video_smart, url, fmt=SMART_FMT_1080)
            size = os.path.getsize(video_path)
            logging.info(f"[VIDEO] downloaded {format_bytes(size)} → {video_path}")

            # 3) отправка и получение file_id
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

            # 4) кладём в кеш
            cache_put(
                content_key, variant, kind="video",
                file_id=file_id, file_unique_id=file_unique_id,
                width=width, height=height, duration=duration, size=size,
                fmt_used=SMART_FMT_1080, title=title, source_url=url
            )
            logging.info(f"[CACHE SAVE] {content_key} [{variant}]")

            # 5) заменяем плейсхолдер на итог
            await context.bot.edit_message_media(
                inline_message_id=inline_id,
                media=InputMediaVideo(media=file_id, caption=f"Видео готово: {url}")
            )

        # except subprocess.CalledProcessError as e:
            # err = e.stderr if isinstance(e.stderr, str) else (e.stderr.decode() if e.stderr else str(e))
            # logging.error(f"[VIDEO] yt-dlp error: {err}")
            # await _set_caption("Не удалось скачать видео.")
            # return
        except Exception as e:
            logging.error(f"[VIDEO] fail: {e}")
            await _set_caption("Не удалось скачать видео.")
            return
        finally:
            try:
                if thumb and os.path.exists(thumb):
                    os.remove(thumb)
                if video_path and os.path.exists(video_path):
                    os.remove(video_path)
            except Exception:
                pass
        return

    # 🎵 Аудио: сразу best (mp3). Хочешь m4a по умолчанию — поменяй 'mp3' на 'm4a'
    if action == "aauto":
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
            audio_path = download_audio(url, fmt="mp3")
            size = os.path.getsize(audio_path)

            # ← вот этих двух строк у тебя не было
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
            return
        except Exception as e:
            logging.error(f"[AUDIO] fail: {e}")
            kb = build_full_format_keyboard(task_id, url)
            await _set_caption("Не удалось получить аудио. Выбери вариант:", kb)
            return
        finally:
            try:
                if audio_path and os.path.exists(audio_path):
                    os.remove(audio_path)
            except Exception:
                pass
        return

# === функции БД ===
def db_init():
    global db_conn
    db_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    db_conn.row_factory = sqlite3.Row
    cur = db_conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS cache (
        content_key   TEXT NOT NULL,        -- например "youtube:_v1ZS_IaJKA"
        variant_key   TEXT NOT NULL,        -- например "video:smart1080", "video:fmt=137+140", "audio:mp3", "anim:50"
        kind          TEXT NOT NULL,        -- 'video' | 'audio' | 'animation'
        file_id       TEXT NOT NULL,        -- Telegram file_id, валидный для ЭТОГО бота
        file_unique_id TEXT,
        width         INTEGER,
        height        INTEGER,
        duration      INTEGER,
        size          INTEGER,
        fmt_used      TEXT,                 -- какой селектор или itag мы использовали
        title         TEXT,
        source_url    TEXT,
        created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (content_key, variant_key)
    );
    """)
    db_conn.commit()
    logging.info(f"[DB] cache at {DB_PATH}")

def cache_get(content_key: str, variant_key: str):
    cur = db_conn.cursor()
    cur.execute("SELECT * FROM cache WHERE content_key=? AND variant_key=?", (content_key, variant_key))
    return cur.fetchone()

def cache_put(content_key: str, variant_key: str, *, kind: str, file_id: str, file_unique_id: Optional[str],
              width: Optional[int], height: Optional[int], duration: Optional[int], size: Optional[int],
              fmt_used: str, title: Optional[str], source_url: str):
    cur = db_conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO cache(content_key, variant_key, kind, file_id, file_unique_id,
            width, height, duration, size, fmt_used, title, source_url)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (content_key, variant_key, kind, file_id, file_unique_id, width, height, duration, size, fmt_used, title, source_url))
    db_conn.commit()
    logging.info(f"[DB] saved {content_key} [{variant_key}] → {file_id}")

def ytdlp_info(url: str) -> Dict[str, Any]:
    r = subprocess.run(["yt-dlp", "-J", url], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)

def extract_title_artist(url: str, fallback_title: Optional[str] = None) -> Tuple[str, str]:
    """Вернёт (полный заголовок, артист) для красивой карточки в Telegram."""
    try:
        info = ytdlp_info(url)
        title_full = info.get("track") or info.get("title") or fallback_title or "Audio"
        artist = info.get("artist") or info.get("uploader") or ""
        return title_full, artist
    except Exception:
        return fallback_title or "Audio", ""

def get_content_key_and_title(url: str):
    url = normalize_youtube_url(url)
    # 1) пробуем обычный -J
    try:
        r = subprocess.run(
            ["yt-dlp", "--ignore-config", "-J", "--no-playlist", url],
            capture_output=True, text=True, check=True
        )
        info = json.loads(r.stdout)
        extractor = (info.get("extractor_key") or info.get("extractor") or "unknown")
        vid = info.get("id")
        title = info.get("title") or "video"
        if extractor.lower() == "youtube" and not vid:
            vid = extract_youtube_id(url)
        if extractor.lower() == "youtube" and vid:
            return f"YouTube:{vid}", title
        if vid and extractor:
            return f"{extractor}:{vid}", title
        # если id нет — падаем в sha1
    except subprocess.CalledProcessError as e:
        logging.warning(f"[CKEY] -J failed: {e}")

    # 2) устойчивый фолбэк для YouTube
    yid = extract_youtube_id(url)
    if yid:
        return f"YouTube:{yid}", None

    # 3) универсальный фолбэк — sha1 от нормализованного URL
    return "urlsha1:" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:16], None

def detect_media_kind_and_key(url: str) -> Tuple[str, str, Optional[str]]:
    """
    -> (mode, content_key, title)
    mode: 'video' | 'audio' | 'unknown'
    """
    try:
        info = ytdlp_info(url)
        fmts = info.get("formats", []) or []
        has_video = any(f.get("vcodec") not in (None, "none") for f in fmts)
        has_audio_only = any((f.get("vcodec") in (None, "none")) and (f.get("acodec") not in (None, "none")) for f in fmts)
        extr = info.get("extractor") or info.get("extractor_key") or "unknown"
        vid  = info.get("id") or ""
        title = info.get("title")
        key = f"{extr}:{vid}" if vid else f"{extr}:{hash(url)}"
        mode = "video" if has_video else ("audio" if has_audio_only else "unknown")
        logging.info(f"[AUTO/DETECT] {mode} key={key}")
        return mode, key, title
    except Exception as e:
        logging.warning(f"[AUTO/DETECT] probe failed: {e}")
        key, title = get_content_key_and_title(url)
        return "unknown", key, title
        
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Используй инлайн @бота или пришли ссылку")

def main():
    global pyro_app

    app = ApplicationBuilder().token(TOKEN).build()

    # PTB handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.User(OWNER_ID) & filters.ATTACHMENT,
            send_file_ids,
        )
    )
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.Chat(chat_id=CACHE_CHAT_ID) & filters.VIDEO, cache_listener))

    async def on_startup(_app):
        global pyro_app, BOT_USERNAME, BOT_ID
        db_init()
        # узнаём username/id бота
        me_bot = await _app.bot.get_me()
        BOT_USERNAME = me_bot.username  # без '@'
        BOT_ID = me_bot.id
        logging.info(f"[BOT] Я @{BOT_USERNAME} (id={BOT_ID})")

        # стартуем юзербота
        if PYRO_API_ID and PYRO_API_HASH:
            logging.info("[PYRO] Стартуем Pyrogram…")
            logging.info(f"[PYRO] Будем использовать сессию: {PYRO_SESSION} (.session: {PYRO_SESSION+'.session'}) "
             f"exists={os.path.exists(PYRO_SESSION+'.session')}")
            pyro_app = PyroClient(
                PYRO_SESSION,  # "Pixel7_session" или путь, который ты задаёшь в PYRO_SESSION
                api_id=PYRO_API_ID,
                api_hash=PYRO_API_HASH,
                device_model="Google Pixel 7",
                system_version="Android 14",
                app_version="10.3.1",
            )
            await pyro_app.start()
            me = await pyro_app.get_me()
            logging.info(f"[PYRO] Запущен как @{getattr(me, 'username', None) or me.first_name} (id={me.id})")
        else:
            logging.warning("[PYRO] Пропускаем запуск Pyrogram — нет API_ID/API_HASH.")

    async def on_shutdown(_app):
        global pyro_app
        if pyro_app:
            logging.info("[PYRO] Останавливаем Pyrogram…")
            await pyro_app.stop()

    app.post_init = on_startup
    app.post_shutdown = on_shutdown

    logging.info("[БОТ] Готов к работе")
    app.run_polling()


if __name__ == "__main__":
    main()
