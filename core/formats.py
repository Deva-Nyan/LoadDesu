"""Helpers for representing yt-dlp format information."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class FormatOption:
    """Single downloadable format entry shown to the user."""

    format_id: str
    label: str


@dataclass
class FormatSummary:
    """Collection of format options grouped by their characteristics."""

    title: str
    progressive: List[FormatOption] = field(default_factory=list)
    video_only: List[FormatOption] = field(default_factory=list)
    audio_only: List[FormatOption] = field(default_factory=list)
