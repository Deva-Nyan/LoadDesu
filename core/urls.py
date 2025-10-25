"""Utilities for locating and validating URLs in user messages."""

from __future__ import annotations

import re
from typing import Iterable, Optional
from urllib.parse import urlparse

# Regular expression borrowed from urllib documentation for quick URL detection.
URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)


# What: Pull the first HTTP(S) URL from an arbitrary text snippet.
# Inputs: ``text`` - user-provided message or caption string.
# Outputs: A normalised URL string or ``None`` when nothing is detected.
def extract_first_url(text: str) -> Optional[str]:
    match = URL_PATTERN.search(text)
    return match.group(0) if match else None


# What: Determine whether a URL's hostname is whitelisted.
# Inputs: ``url`` - HTTP(S) link; ``allowed_domains`` - collection of base domains.
# Outputs: ``True`` when the hostname equals/endswith one of the allowed domains.
def is_domain_allowed(url: str, allowed_domains: Iterable[str]) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    for domain in allowed_domains:
        domain = domain.lower()
        if host == domain or host.endswith(f".{domain}"):
            return True
    return False
