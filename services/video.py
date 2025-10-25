import os, subprocess, logging
from pathlib import Path
from typing import Optional
from config import MAX_TG_SIZE, GIF_FMT
from utils.text import format_bytes

def get_video_info(video_path: str):
    try:
        cmd = [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,duration",
            "-of", "default=noprint_wrappers=1:nokey=1", video_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        lines = result.stdout.strip().split("\n")
        width, height, duration = int(lines[0]), int(lines[1]), float(lines[2])
        return int(duration), width, height
    except Exception as e:
        logging.warning(f"⚠ Не удалось получить параметры видео: {e}")
        return 0, 640, 360

def generate_thumbnail(video_path: str) -> Optional[str]:
    out_path = Path(video_path).with_suffix(".thumb.jpg")
    for ss in ["00:00:02", "00:00:00.5", "00:00:00"]:
        try:
            subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-ss", ss, "-i", video_path, "-frames:v", "1",
                "-vf", "scale=min(320\\,iw):min(320\\,ih):force_original_aspect_ratio=decrease",
                "-q:v", "5", str(out_path),
            ], check=True, capture_output=True)
            if os.path.exists(out_path):
                if os.path.getsize(out_path) > 200 * 1024:
                    subprocess.run([
                        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-i", str(out_path),
                        "-vf", "scale=min(320\\,iw):min(320\\,ih):force_original_aspect_ratio=decrease",
                        "-q:v", "10", str(out_path),
                    ], check=True, capture_output=True)
                return str(out_path)
        except Exception:
            continue
    logging.warning("[THUMBNAIL] Не удалось создать превью")
    return None

def video_to_tg_animation(in_path: str, target_mb: int = 50) -> str:
    base, _ = os.path.splitext(in_path)
    out = base + ".anim.mp4"
    attempts = [(480, 30, 23), (360, 30, 24), (320, 24, 26)]
    for w, fps, crf in attempts:
        subprocess.run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", in_path, "-an",
            "-vf", f"scale=min({w}\\,iw):-2:flags=lanczos,fps={fps}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "baseline",
            "-movflags", "+faststart", "-crf", str(crf), out,
        ], check=True)
        if os.path.getsize(out) <= target_mb * 1024 * 1024:
            break
    return out

def video_to_gif(in_path: str) -> str:
    base = os.path.splitext(in_path)[0]
    palette = base + "_palette.png"
    out = base + ".gif"

    def make_gif(scale_w: int, fps: int):
        vf = f"fps={fps},scale={scale_w}:-1:flags=lanczos"
        subprocess.run(["ffmpeg", "-y", "-i", in_path, "-vf", f"{vf},palettegen", palette], check=True, capture_output=True)
        subprocess.run(["ffmpeg", "-y", "-i", in_path, "-i", palette, "-filter_complex", f"{vf}[x];[x][1:v]paletteuse=dither=sierra2_4a", "-loop", "0", out], check=True, capture_output=True)
        if os.path.exists(palette):
            os.remove(palette)

    for w, fps in [(480, 12), (360, 10), (320, 8)]:
        make_gif(w, fps)
        sz = os.path.getsize(out)
        logging.info(f"[GIF] {out} = {format_bytes(sz)} (лимит {format_bytes(MAX_TG_SIZE)})")
        if sz <= MAX_TG_SIZE:
            return out
        else:
            try: os.remove(out)
            except Exception: pass
    make_gif(320, 6)
    return out

def download_gif_from_url(url: str, download_animation_source, gif_fmt: str = GIF_FMT) -> str:
    mp4_path = download_animation_source(url, gif_fmt)
    return video_to_gif(mp4_path)