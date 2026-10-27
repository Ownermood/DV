"""Progress UI: one clean card for every download stage.

    <premium icon> <b>Downloading</b>
    <blockquote><b>▰▰▱▱▱ 40%</b></blockquote>

5 blocks, moving in 20 % steps. Icons are premium (custom) emoji once Telegram has
accepted them for this bot (see COMMANDS/ui_kit.py), plain emoji before that.
normalize_status() turns the old multi-line status texts ("Total Progress",
"Downloading using format: …", "Please wait…") into this card.
"""
from __future__ import annotations

import re

from pyrogram import enums

FILLED, EMPTY = "▰", "▱"
WIDTH = 5

# kind -> (ui_kit emoji key, title)
_KINDS = {
    "search": ("search", "Searching"),
    "download": ("down", "Downloading"),
    "process": ("bolt", "Processing"),
    "upload": ("phone", "Uploading"),
    "done": ("ok", "Done"),
}
_PREMIUM_RE = re.compile(r'<(?:tg-emoji emoji-id|emoji id)="\d+">(.*?)</(?:tg-)?emoji>')
_TAG_RE = re.compile(r"<[^>]+>")
_PCT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")


def _icon(key: str) -> str:
    from COMMANDS.ui_kit import E, premium_on, state_get
    char, cid = E[key]
    # legacy senders have no retry: premium only after Telegram accepted it once
    if premium_on() and state_get("premium_ok"):
        return f'<emoji id="{cid}">{char}</emoji>'
    return char


STEP = 100 // WIDTH   # 20 %


def step(percent) -> int:
    """Percent rounded down to a block step (0, 20, 40 … 100)."""
    p = max(0.0, min(100.0, float(percent or 0)))
    return int(p // STEP) * STEP


def bar_blocks(blocks: int, width: int = WIDTH) -> str:
    blocks = max(0, min(width, int(blocks)))
    return FILLED * blocks + EMPTY * (width - blocks)


def bar(percent: float, width: int = WIDTH) -> str:
    return bar_blocks(step(percent) * width // 100, width)


def progress_card(kind: str, percent: float, process: str = "", detail: str = "") -> str:
    """The card. `process` (old status lines) is ignored; `detail` is one short plain line."""
    key, title = _KINDS[kind]
    body = f"{bar(percent)} {step(percent)}%"
    if detail:
        body += "\n" + _TAG_RE.sub("", str(detail))[:120]
    return f"{_icon(key)} <b>{title}</b>\n<blockquote><b>{body}</b></blockquote>"


def normalize_status(text: str):
    """Old status text -> card, or None if text is not a progress status."""
    if not isinstance(text, str) or not text:
        return None
    plain = _TAG_RE.sub("", _PREMIUM_RE.sub(r"\1", text)).lower()
    markers = ("total progress", "please wait", "processing", "downloading using format", "detected hls",
               "trying gallery-dl", "uploading audio file", "downloading with progress")
    if not any(m in plain for m in markers) and plain.strip() not in ("⏳", "⌛"):
        return None
    pcts = _PCT_RE.findall(plain)
    pct = float(pcts[-1]) if pcts else 0.0
    if "upload" in plain:
        return progress_card("upload", pct or 100)
    if "processing" in plain or pct >= 100:
        return progress_card("process", 100)
    if "please wait" in plain or plain.strip() in ("⏳", "⌛"):
        return progress_card("search", 0)
    return progress_card("download", pct)


def strip_premium(text: str) -> str:
    return _PREMIUM_RE.sub(r"\1", text)


def edit_card(edit_fn, chat_id, message_id, text: str):
    """Edit with HTML; if Telegram rejects premium emoji, retry with plain emoji.
    Downloading / uploading cards get the ❌ Cancel button."""
    kw = {"parse_mode": enums.ParseMode.HTML}
    if any(f"<b>{_KINDS[k][1]}</b>" in text for k in ("download", "upload")):
        try:
            from HELPERS.cancel import markup
            kw["reply_markup"] = markup(chat_id)
        except Exception:
            pass
    try:
        result = edit_fn(chat_id, message_id, text, **kw)
    except Exception:
        result = None
    if result is None and _PREMIUM_RE.search(text):
        result = edit_fn(chat_id, message_id, strip_premium(text), **kw)
    return result
