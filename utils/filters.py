from telegram.ext import filters


def build_media_filter():
    F = filters
    parts = []
    for name in ("PHOTO", "VIDEO", "ANIMATION", "AUDIO", "VOICE", "VIDEO_NOTE"):
        if hasattr(F, name):
            parts.append(getattr(F, name))
    if hasattr(F, "Document") and hasattr(F.Document, "ALL"):
        parts.append(F.Document.ALL)
    elif hasattr(F, "DOCUMENT"):
        parts.append(F.DOCUMENT)
    if hasattr(F, "Sticker") and hasattr(F.Sticker, "ALL"):
        parts.append(F.Sticker.ALL)
    elif hasattr(F, "STICKER"):
        parts.append(F.STICKER)
    if hasattr(F, "ATTACHMENT"):
        parts.append(F.ATTACHMENT)
    if not parts:
        raise RuntimeError("Не удалось собрать media-фильтр для этой версии PTB")
    f = parts[0]
    for p in parts[1:]:
        f |= p
    return f