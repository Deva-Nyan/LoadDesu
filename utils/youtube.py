# utils/youtube.py
import json, subprocess, logging
from typing import Optional,Dict, Any
from urllib.parse import urlparse, parse_qs

YID_LEN = 11

def extract_youtube_id(url: str) -> Optional[str]:
    """
    Возвращает 11-символьный ID из ссылок:
    - https://www.youtube.com/watch?v=XXXXXXXXXXX
    - https://youtu.be/XXXXXXXXXXX
    - https://www.youtube.com/shorts/XXXXXXXXXXX
    - https://www.youtube.com/embed/XXXXXXXXXXX
    """
    u = urlparse(url)

    # youtu.be/<id>
    if u.netloc.endswith("youtu.be"):
        cand = u.path.lstrip("/").split("/")[0]
        return cand if len(cand) == YID_LEN else None

    # /shorts/<id> или /embed/<id>
    for prefix in ("/shorts/", "/embed/"):
        if prefix in u.path:
            cand = u.path.split(prefix, 1)[1].split("/")[0]
            return cand if len(cand) == YID_LEN else None

    # watch?v=<id>
    q = parse_qs(u.query)
    cand = (q.get("v") or [None])[0]
    return cand if cand and len(cand) == YID_LEN else None