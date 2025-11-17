# downloader.py
import os
import subprocess
import logging
from config import (SAVE_DIR, SMART_FMT_1080, GIF_FMT, DEFAULT_UA, 
                    COOKIES_FILE, COOKIES_FROM_BROWSER)
from utils import _pick_single_path, _origin


def download_video_smart(url: str, fmt: str = SMART_FMT_1080) -> str:
    """Скачивает видео (MP4). При ошибке пробует ретрай с Referer/UA/cookies."""
    logging.info(f"[SMART] -f \"{fmt}\"")

    base_cmd = [
        "yt-dlp",
        "-f", fmt,
        "--merge-output-format", "mp4", 
        "--no-playlist",
        "--no-simulate",
        "--print", "after_move:filepath",
        "--remote-components", "ejs:github",
        "-o", os.path.join(SAVE_DIR, "%(title)s [%(id)s].%(ext)s"),
        url,
    ]
    try:
        r = subprocess.run(base_cmd, capture_output=True, text=True, check=True)
        path = _pick_single_path(r.stdout)
        logging.info(f"[SMART] saved {path} ({os.path.getsize(path)} bytes)")
        return path
    except subprocess.CalledProcessError as e:
        err = e.stderr if isinstance(e.stderr, str) else (e.stderr.decode() if e.stderr else str(e))
        logging.error(f"[SMART] primary yt-dlp failed:\n{err}")

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
        logging.info(f"[SMART] retry saved {path} ({os.path.getsize(path)} bytes)")
        return path


def download_video_with_format(url: str, fmt_id: str) -> str:
    """Скачивает видео с указанным форматом и приводит к mp4"""
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
        logging.info(f"[DL] Файл скачан: {path} ({size} bytes)")
    except Exception as e:
        logging.warning(f"[DL] Не удалось получить размер: {e}")
    return path


def download_animation_source(url: str) -> str:
    """Скачиваем умеренный исходник под анимацию (низкая высота, mp4)"""
    cmd = [
        "yt-dlp", "-f", GIF_FMT,
        "--no-playlist", "--no-simulate",
        "--print", "after_move:filepath",
        "-o", os.path.join(SAVE_DIR, "%(title)s [%(id)s].%(ext)s"),
        url,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return r.stdout.strip()


def download_gif_from_url(url: str) -> str:
    """Скачивает ролик с умеренным качеством и конвертит в GIF"""
    from video_processing import video_to_gif
    
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


def download_audio(url: str, fmt: str = "mp3") -> str:
    """Скачивает аудио в указанном формате"""
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
