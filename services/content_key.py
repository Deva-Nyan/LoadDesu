#services/content_key.py
import json, subprocess, logging, hashlib
from typing import Dict, Any, Optional, Tuple
from services.ytdlp import ytdlp_info
from utils.text import normalize_youtube_url
from utils.youtube import extract_youtube_id

def _canon_extractor(name: str) -> str:
    # всегда нижний регистр, чтобы ключ был "youtube:<id>"
    return (name or "unknown").lower()

def canon_key(key: str) -> str:
    # "YouTube:abc" -> "youtube:abc"
    if not key or ":" not in key:
        return key
    extractor, rest = key.split(":", 1)
    return f"{_canon_extractor(extractor)}:{rest}"

def get_content_key_and_title(url: str):
    url = normalize_youtube_url(url)
    try:
        info = ytdlp_info(url)  # ✅ берём JSON через services/ytdlp (с куками)
        extractor = (info.get("extractor_key")
                     or info.get("extractor")
                     or "unknown")
        extractor = extractor.lower()
        vid = info.get("id")
        title = info.get("title") or "video"

        # иногда yt-dlp даёт extractor=YouTube, но id пустой → fallback
        if extractor == "youtube" and not vid:
            vid = extract_youtube_id(url)

        if vid:
            return f"{extractor}:{vid}", title
    except Exception as e:
        logging.warning(f"[CKEY] ytdlp_info failed: {e}")

    # fallback: пробуем вытащить ID сами
    yid = extract_youtube_id(url)
    if yid:
        return "youtube:" + yid, None

    # если вообще ничего не получилось → хэш URL
    return "urlsha1:" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:16], None

def detect_media_kind_and_key(url: str):
    """
    -> (mode, content_key, title)
    mode: 'video' | 'audio' | 'unknown'
    """
    try:
        info = ytdlp_info(url)
        fmts = info.get("formats", []) or []
        has_video = any(f.get("vcodec") not in (None, "none") for f in fmts)
        has_audio_only = any((f.get("vcodec") in (None, "none")) and (f.get("acodec") not in (None, "none")) for f in fmts)

        extractor = _canon_extractor(info.get("extractor") or info.get("extractor_key") or "unknown")
        vid = info.get("id") or ""
        title = info.get("title")

        key = f"{extractor}:{vid}" if vid else f"{extractor}:{hash(url)}"
        mode = "video" if has_video else ("audio" if has_audio_only else "unknown")
        return mode, key, title
    except Exception:
        key, title = get_content_key_and_title(url)
        return "unknown", key, title

def extract_title_artist(url: str, fallback_title: Optional[str] = None) -> Tuple[str, str]:
    try:
        info = ytdlp_info(url)
        title_full = info.get("track") or info.get("title") or fallback_title or "Audio"
        artist = info.get("artist") or info.get("uploader") or ""
        return title_full, artist
    except Exception:
        return fallback_title or "Audio", ""

def probe_formats(url: str) -> Dict[str, Any]:
    try:
        info = ytdlp_info(url)
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
            ext = f.get("ext"); h = f.get("height"); fps = f.get("fps"); kbps = f.get("tbr")
            sz = f.get("filesize") or f.get("filesize_approx")
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
        return {"progressive": progressive[:10], "merged": merged[:10], "video_only": video_only[:10], "audio_only": audio_only[:8]}
    except Exception as e:
        logging.warning(f"[FMT] Не удалось получить список форматов: {e}")
        return {"progressive": [], "merged": [], "video_only": [], "audio_only": []}