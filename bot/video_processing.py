# video_processing.py
import os
import subprocess
import logging
from pathlib import Path
from typing import Optional
from config import MAX_TG_SIZE


def get_video_info(video_path: str):
    """Определяет длительность, ширину и высоту видео"""
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
        return 0, 640, 360


def generate_thumbnail(video_path: str) -> Optional[str]:
    """Делает JPEG-превью ≤320px, ≤200KB"""
    out_path = Path(video_path).with_suffix(".thumb.jpg")
    tries = ["00:00:02", "00:00:00.5", "00:00:00"]

    for ss in tries:
        try:
            subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-ss", ss, "-i", video_path,
                "-frames:v", "1",
                "-vf", "scale=min(320\\,iw):min(320\\,ih):force_original_aspect_ratio=decrease",
                "-q:v", "5",
                str(out_path),
            ], check=True, capture_output=True)

            if os.path.exists(out_path):
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


def video_to_gif(in_path: str) -> str:
    """Конвертирует mp4 в оптимизированную GIF ≤50MB"""
    base = os.path.splitext(in_path)[0]
    palette = base + "_palette.png"
    out = base + ".gif"

    def make_gif(scale_w: int, fps: int):
        vf = f"fps={fps},scale={scale_w}:-1:flags=lanczos"
        subprocess.run([
            "ffmpeg", "-y", "-i", in_path,
            "-vf", f"{vf},palettegen",
            palette
        ], check=True, capture_output=True)
        subprocess.run([
            "ffmpeg", "-y", "-i", in_path, "-i", palette,
            "-filter_complex", f"{vf}[x];[x][1:v]paletteuse=dither=sierra2_4a",
            "-loop", "0",
            out
        ], check=True, capture_output=True)
        if os.path.exists(palette):
            os.remove(palette)

    try_order = [(480, 12), (360, 10), (320, 8)]
    for w, fps in try_order:
        make_gif(w, fps)
        sz = os.path.getsize(out)
        logging.info(f"[GIF] {out} = {sz} bytes")
        if sz <= MAX_TG_SIZE:
            return out
        else:
            try:
                os.remove(out)
            except Exception:
                pass

    make_gif(320, 6)
    return out


def compress_video(path: str) -> str:
    """Сжимает видео через ffmpeg под лимит MAX_TG_SIZE (двухпроходное)"""
    size = os.path.getsize(path)
    if size <= MAX_TG_SIZE:
        logging.info(f"[COMPRESSION] Уже в лимите: {size} ≤ {MAX_TG_SIZE}")
        return path

    duration, src_w, src_h = get_video_info(path)
    logging.info(f"[COMPRESSION] Исходник: {size} bytes, duration={duration:.2f}s, {src_w}x{src_h}")

    if duration <= 0:
        target_total_kbps = 950
    else:
        target_bits = int(MAX_TG_SIZE * 0.96 * 8)
        target_total_kbps = max(384, target_bits // max(1, int(duration)) // 1000)

    audio_kbps = 128
    video_kbps = max(300, target_total_kbps - audio_kbps)

    scale_vf = "scale=-2:720"
    base, _ = os.path.splitext(path)
    out = f"{base}_compressed.mp4"

    logging.info(f"[COMPRESSION] Цель: ~{MAX_TG_SIZE} | total≈{target_total_kbps}kbps")

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
    logging.info(f"[COMPRESSION] Результат: {new_size} bytes")

    if new_size > MAX_TG_SIZE:
        logging.warning("[COMPRESSION] Всё ещё больше лимита — оставляем оригинал")
        try:
            os.remove(out)
        except Exception:
            pass
        return path

    return out
