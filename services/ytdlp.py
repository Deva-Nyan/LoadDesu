import os, json, subprocess, logging
from typing import Dict, Any
from config import SAVE_DIR, DEFAULT_UA, COOKIES_FILE, COOKIES_FROM_BROWSER, SMART_FMT_1080
from utils.text import origin

def _pick_single_path(stdout: str) -> str:
    lines = [ln.strip() for ln in stdout.splitlines() if ln.strip()]
    if not lines:
        raise RuntimeError("yt-dlp did not print any file path")
    if len(lines) > 1:
        logging.warning("[YTDLP] multiple outputs detected. Using the last one")
    return lines[-1]

def ytdlp_info(url: str) -> Dict[str, Any]:
    r = subprocess.run(["yt-dlp", "-J", url], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)

def download_video_with_format(url: str, fmt_id: str) -> str:
    cmd = [
        "yt-dlp", "-f", fmt_id,
        "--merge-output-format", "mp4",
        "--no-simulate", "--restrict-filenames",
        "--print", "after_move:filepath",
        "-o", os.path.join(SAVE_DIR, "%(title)s [%(id)s].%(ext)s"), url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout.strip()

def download_video_smart(url: str, fmt: str = SMART_FMT_1080) -> str:
    base_cmd = [
        "yt-dlp", "-f", fmt,
        "--merge-output-format", "mp4",
        "--no-playlist", "--no-simulate",
        "--print", "after_move:filepath",
        "-o", os.path.join(SAVE_DIR, "%(title)s [%(id)s].%(ext)s"), url,
    ]
    try:
        r = subprocess.run(base_cmd, capture_output=True, text=True, check=True)
        return _pick_single_path(r.stdout)
    except subprocess.CalledProcessError as e:
        err = e.stderr if isinstance(e.stderr, str) else (e.stderr.decode() if e.stderr else str(e))
        logging.error(f"[SMART] primary yt-dlp failed:\n{err}")
        cmd = [
            "yt-dlp", "-f", "best", "--no-playlist", "--max-downloads", "1", "--no-simulate",
            "--add-header", f"Referer: {origin(url)}", "--user-agent", DEFAULT_UA,
            "--print", "after_move:filepath",
            "-o", os.path.join(SAVE_DIR, "%(title)s [%(id)s].%(ext)s"), url,
        ]
        if COOKIES_FILE:
            cmd += ["--cookies", COOKIES_FILE]
        elif COOKIES_FROM_BROWSER:
            cmd += ["--cookies-from-browser", COOKIES_FROM_BROWSER]
        r2 = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return _pick_single_path(r2.stdout)

def download_audio(url: str, fmt: str = "mp3") -> str:
    base = [
        "yt-dlp", "-x", "--audio-format", fmt, "--audio-quality", "0",
        "--no-playlist", "--no-simulate", "--restrict-filenames",
        "--print", "after_move:filepath",
        "-o", os.path.join(SAVE_DIR, "%(title)s [%(id)s].%(ext)s"), url,
    ]
    cmd = (["yt-dlp", "-f", "bestaudio[ext=m4a]/bestaudio"] + base[1:]) if fmt == "m4a" else base
    r = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return r.stdout.strip()

def download_animation_source(url: str, gif_fmt: str) -> str:
    cmd = [
        "yt-dlp", "-f", gif_fmt, "--no-playlist", "--no-simulate",
        "--print", "after_move:filepath",
        "-o", os.path.join(SAVE_DIR, "%(title)s [%(id)s].%(ext)s"), url,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return r.stdout.strip()