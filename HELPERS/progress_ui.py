"""Progress UI: one place for download/upload progress cards.

Renders (HTML parse mode):
    <emoji> <b>Title</b>

    <blockquote>▬▬▬▬▭▭▭▭▭▭▭▭ 33%</blockquote>

Premium (custom) emoji are optional. Set env PREMIUM_EMOJI_ENABLED=true and any of
PREMIUM_EMOJI_SEARCH / _DOWNLOAD / _UPLOAD / _DONE to a custom emoji id.
Telegram only renders custom emoji sent by bots that own a Fragment username;
otherwise the plain fallback emoji is used (retry happens automatically).
"""
from __future__ import annotations

import html
import os
import re

from pyrogram import enums

FILLED, EMPTY = "▬", "▭"

_KINDS = {
    "search": ("SEARCH", "⚡", "Searching"),
    "download": ("DOWNLOAD", "📥", "Downloading"),
    "upload": ("UPLOAD", "📤", "Uploading"),
    "done": ("DONE", "✅", "Done"),
}
_TG_EMOJI_RE = re.compile(r'<tg-emoji emoji-id="\d+">(.*?)</tg-emoji>')


def _premium_enabled() -> bool:
    return os.getenv("PREMIUM_EMOJI_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def bar_blocks(blocks: int, width: int = 10) -> str:
    blocks = max(0, min(width, int(blocks)))
    return FILLED * blocks + EMPTY * (width - blocks)


def bar(percent: float, width: int = 12) -> str:
    percent = max(0.0, min(100.0, float(percent or 0)))
    return bar_blocks(int(percent / 100 * width), width)


def _icon(kind: str) -> str:
    env_key, fallback, _ = _KINDS[kind]
    emoji_id = os.getenv(f"PREMIUM_EMOJI_{env_key}", "").strip()
    if _premium_enabled() and emoji_id.isdigit():
        return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'
    return fallback


def progress_card(kind: str, percent: float, process: str = "", detail: str = "") -> str:
    """Build the HTML card. `process`/`detail` are escaped here."""
    _, _, title = _KINDS[kind]
    head = f"{_icon(kind)} <b>{title}</b>"
    if process:
        head += f"\n{html.escape(str(process))}"
    body = f"{bar(percent)} {float(percent or 0):.0f}%"
    if detail:
        body += f"\n{html.escape(str(detail))}"
    return f"{head}\n\n<blockquote>{body}</blockquote>"


def strip_premium(text: str) -> str:
    return _TG_EMOJI_RE.sub(r"\1", text)


def edit_card(edit_fn, chat_id, message_id, text: str):
    """Edit with HTML; if Telegram rejects premium emoji, retry with plain emoji."""
    try:
        result = edit_fn(chat_id, message_id, text, parse_mode=enums.ParseMode.HTML)
    except Exception:
        result = None
    if result is None and "<tg-emoji" in text:
        result = edit_fn(chat_id, message_id, strip_premium(text), parse_mode=enums.ParseMode.HTML)
    return result
