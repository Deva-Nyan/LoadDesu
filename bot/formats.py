# formats.py
import subprocess
import json
import logging
from telegram import InlineKeyboardMarkup, InlineKeyboardButton


def probe_formats(url: str):
    """Получает список доступных форматов через yt-dlp -J"""
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
    """Строит полную клавиатуру с форматами"""
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

    logging.info(f"[KB/BUILD] full-kb для {url}: rows={len(btns)}")
    if len(btns) == 1:
        btns.insert(0, [InlineKeyboardButton("best (автовыбор)", callback_data=f"fmt|{task_id}|bv*+ba/b")])
    return InlineKeyboardMarkup(btns)
