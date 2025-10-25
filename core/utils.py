"""Utility helpers used across the rewritten bot modules."""

from __future__ import annotations

# The standard library imports are grouped to keep the module tidy.
import math


# The handlers are intentionally kept lightweight, so reusable helpers such as
# byte-size formatting live in this module.


# What: Convert a byte counter into a short human readable string.
# Inputs: ``size`` - the file size in bytes as an integer.
# Outputs: A string like ``"12.4 MB"``.
def format_bytes(size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    if size <= 0:
        return "0 B"
    magnitude = min(int(math.log(size, 1024)), len(units) - 1)
    value = size / (1024**magnitude)
    if magnitude == 0:
        return f"{int(value)} {units[magnitude]}"
    return f"{value:.2f} {units[magnitude]}"
