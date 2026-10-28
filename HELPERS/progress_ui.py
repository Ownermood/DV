"""Progress UI: one clean card for every download stage.

    <premium icon> <b>Downloading</b>
    <blockquote><b>▰▰▱▱▱ 40%
    120 / 300 MB · 2.5 MB/s</b></blockquote>

5 blocks, moving in 20 % steps; the next block blinks on every update. Icons are premium (custom) emoji once Telegram has
accepted them for this bot (see COMMANDS/ui_kit.py), plain emoji before that.
normalize_status() turns the old multi-line status texts ("Total Progress",
"Downloading using format: …", "Please wait…") into this card.
"""
from __future__ import annotations

import itertools
import re
import threading
import time

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


_tick = itertools.count()


def _blink() -> bool:
    """Alternates on every card build, so the card changes even when the % does not."""
    return next(_tick) % 2 == 0


def bar(percent: float, width: int = WIDTH, moving: bool = False) -> str:
    full = step(percent) * width // 100
    text = bar_blocks(full, width)
    if moving and full < width and _blink():  # the next block blinks ▱ -> ▰ -> ▱
        text = FILLED * (full + 1) + EMPTY * (width - full - 1)
    return text


def progress_card(kind: str, percent: float, process: str = "", detail: str = "") -> str:
    """The card. `process` (old status lines) is ignored; `detail` is one short plain line."""
    key, title = _KINDS[kind]
    body = f"{bar(percent, moving=True)} {int(max(0.0, min(100.0, float(percent or 0))))}%"  # real %, bar in 20 % steps
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


# Progress edits run in the background: a download / upload never waits on Telegram.
# Groups allow about 20 edits a minute, and a FloodWait sleep inside the progress
# callback used to stall the whole transfer there.
GROUP_GAP = 5.0
_edit_lock = threading.Lock()
_busy = set()       # (chat, message) whose previous edit is still running
_chat_last = {}     # group chat -> last progress edit (monotonic)


def edit_later(fn, chat_id, message_id, force=False) -> bool:
    """Run fn() in a thread. Skipped while this card's previous edit is still running,
    and in groups when the chat was edited less than GROUP_GAP seconds ago (unless force)."""
    key = (chat_id, message_id)
    now = time.monotonic()
    with _edit_lock:
        if key in _busy:
            return False
        group = isinstance(chat_id, int) and chat_id < 0
        if group and not force and now - _chat_last.get(chat_id, 0.0) < GROUP_GAP:
            return False
        _busy.add(key)
        if group:
            _chat_last[chat_id] = now

    def run():
        try:
            fn()
        except Exception:
            pass
        finally:
            with _edit_lock:
                _busy.discard(key)
    threading.Thread(target=run, daemon=True).start()
    return True


def edit_card(edit_fn, chat_id, message_id, text: str, force=False):
    """Edit with HTML; if Telegram rejects premium emoji, retry with plain emoji.
    Downloading / uploading cards get the ❌ Cancel button. Runs in the background."""
    kw = {"parse_mode": enums.ParseMode.HTML}
    if any(f"<b>{_KINDS[k][1]}</b>" in text for k in ("download", "upload")):
        try:
            from HELPERS.cancel import markup
            kw["reply_markup"] = markup(chat_id)
        except Exception:
            pass

    def run():
        from HELPERS.cancel import is_set
        if "reply_markup" in kw and is_set(chat_id):
            return  # ❌ Cancel already replaced this card
        try:
            result = edit_fn(chat_id, message_id, text, **kw)
        except Exception:
            result = None
        if result is None and _PREMIUM_RE.search(text):
            edit_fn(chat_id, message_id, strip_premium(text), **kw)
    edit_later(run, chat_id, message_id, force=force)
    return True
