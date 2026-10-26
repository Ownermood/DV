"""Shared UI helpers: premium emoji, coloured buttons, small JSON state, i18n.

Premium (custom) emoji in buttons/messages only render when the bot owner has
Telegram Premium (or the bot has a Fragment username). If Telegram rejects
them, `send`/`edit` retry once in plain mode and switch premium off.
"""
from __future__ import annotations

import json
import re
import os
import threading

from pyrogram import enums
from pyrogram.types import InlineKeyboardButton

from HELPERS.logger import logger

# key -> (fallback emoji, custom_emoji_id)
E = {
    "search": ("🔎", "5445023138297447592"),
    "globe": ("🌐", "5447602197439218445"),
    "back": ("⬅️", "5447506720316225765"),
    "next": ("➡️", "5445350109862720603"),
    "folder": ("📂", "5447282724886839705"),
    "exit": ("🚪", "5447434637880098257"),
    "down": ("⬇️", "5444961234933806330"),
    "ok": ("✅", "5444987348334965906"),
    "check": ("✔️", "5447242579827523388"),
    "plus": ("➕", "5447224884562263112"),
    "minus": ("➖", "5447155993286832278"),
    "medal": ("🏅", "5444931419270839381"),
    "play": ("▶️", "5444883062234053429"),
    "chat": ("💬", "5445140257760639304"),
    "ban": ("🚫", "5447647474984449520"),
    "bolt": ("⚡️", "5445388803223091254"),
    "like": ("👍", "5447153029759397673"),
    "heart": ("❤️", "5444887644964159628"),
    "warn": ("⚠️", "5447381715293074599"),
    "image": ("🖼", "5447588260270341594"),
    "memo": ("📝", "5444889156792646660"),
    "music": ("🎧", "5445276884965291212"),
    "trash": ("🗑", "5445005936953424165"),
    "refresh": ("↪️", "5445365692004071819"),
    "phone": ("📲", "5445059250382469069"),
    "wave": ("👋", "5424611538151437896"),
    "hug": ("🤗", "5424829516331622320"),
    "cool": ("😎", "5424663180838182778"),
    "party": ("🥳", "5424968454228680999"),
    "popcorn": ("🍿", "5424673243946557836"),
    "laptop": ("💻", "5424994438780821530"),
    "box": ("📦", "5422725295889196694"),
    "cry": ("😭", "5424908668283919719"),
    "question": ("❓", "5424700761802023186"),
    "clap": ("👏", "5424679359979986283"),
    "stars": ("🤩", "5424639292230082961"),
    "crown": ("👸", "5424709970211905165"),
}

BOT_TITLE = "Downloder"
BOT_USERNAME = "Downloader_Villanbot"

BLUE = enums.ButtonStyle.PRIMARY
GREEN = enums.ButtonStyle.SUCCESS
RED = enums.ButtonStyle.DANGER

_STATE_FILE = os.path.join("CONFIG", ".ui_state.json")
_lock = threading.RLock()
_state = None


def _load():
    global _state
    if _state is None:
        try:
            with open(_STATE_FILE, "r", encoding="utf-8") as f:
                _state = json.load(f)
        except FileNotFoundError:
            _state = {}
        except Exception as exc:
            logger.warning(f"[ui] state file unreadable, starting fresh: {exc}")
            _state = {}
    return _state


def state_get(key, default=None):
    with _lock:
        return _load().get(key, default)


def state_set(key, value):
    with _lock:
        _load()[key] = value
        tmp = _STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_state, f)
        os.replace(tmp, _STATE_FILE)  # atomic write


def premium_on() -> bool:
    return bool(state_get("premium", True))


def em(key: str) -> str:
    """Emoji for message text (HTML). Premium -> custom emoji entity."""
    char, cid = E[key]
    return f'<emoji id="{cid}">{char}</emoji>' if premium_on() else char


def btn(key, text, cb=None, url=None, style=BLUE) -> InlineKeyboardButton:
    char, cid = E[key] if key else ("", None)
    kw = {"callback_data": cb} if cb else {"url": url}
    if key and premium_on():
        return InlineKeyboardButton(text, icon_custom_emoji_id=cid, style=style, **kw)
    return InlineKeyboardButton(f"{char} {text}".strip(), style=style, **kw)


def _is_emoji_error(exc) -> bool:
    s = str(exc).upper()
    return any(k in s for k in ("EMOJI", "DOCUMENT_INVALID", "PREMIUM", "ICON", "BUTTON_STYLE"))


_QUOTE_TAG = re.compile(r"(</?blockquote[^>]*>)")


def bold(text: str) -> str:
    """House style: every text part bold, inside quotes too."""
    if not text:
        return text
    out = []
    for part in _QUOTE_TAG.split(text.replace("<b>", "").replace("</b>", "")):
        if not part or _QUOTE_TAG.fullmatch(part) or not part.strip():
            out.append(part)
        else:
            lead = part[:len(part) - len(part.lstrip())]
            trail = part[len(part.rstrip()):]
            out.append(f"{lead}<b>{part.strip()}</b>{trail}")
    return "".join(out)


def _call(fn, build, **kw):
    """build() -> (text, markup); retried once without premium emoji on rejection."""
    raw = build
    build = lambda: (lambda tm: (bold(tm[0]), tm[1]))(raw())
    text, markup = build()
    try:
        result = fn(text, markup)
        if premium_on() and "<emoji id=" in (text or "") and not state_get("premium_ok"):
            state_set("premium_ok", True)  # Telegram accepted premium emoji: legacy menus may use them too
        return result
    except Exception as exc:
        if premium_on() and _is_emoji_error(exc):
            logger.warning(f"[ui] premium emoji rejected ({exc}); switching to plain emoji")
            state_set("premium", False)
            state_set("premium_ok", False)
            text, markup = build()
            return fn(text, markup)
        raise


def send(client, chat_id, build, photo=None, **kw):
    if photo:
        return _call(lambda t, m: client.send_photo(chat_id, photo, caption=t, parse_mode=enums.ParseMode.HTML,
                                                    reply_markup=m, **kw), build)
    return _call(lambda t, m: client.send_message(chat_id, t, parse_mode=enums.ParseMode.HTML, reply_markup=m,
                                                  disable_web_page_preview=True, **kw), build)


def edit(client, msg, build):
    """Edit text or caption of `msg` in place."""
    if getattr(msg, "photo", None):
        return _call(lambda t, m: client.edit_message_caption(msg.chat.id, msg.id, t, parse_mode=enums.ParseMode.HTML,
                                                              reply_markup=m), build)
    return _call(lambda t, m: client.edit_message_text(msg.chat.id, msg.id, t, parse_mode=enums.ParseMode.HTML,
                                                       reply_markup=m, disable_web_page_preview=True), build)


# ---------------- UI strings (English only) ----------------
T = {
    "en": {
        "welcome": "{wave} <b>Hey {name}!</b> Welcome to <b>{bot}</b>\n\n<blockquote>{bolt} Send any link and I'll download it in the <b>best quality</b>.\n{music} MP3 and {image} thumbnail on every link.</blockquote>",
        "help": "Help", "admin": "Admin Panel", "back": "Back", "close": "Close",
        "help_text": ("{question} <b>What I can do</b>\n\n<blockquote expandable>"
                      "{bolt} Auto best quality, up to {max_gb} GB. If the best quality is too big, I step down "
                      "(1080p → 720p …) automatically.\n"
                      "{play} YouTube: pick the quality yourself. Too big? I show the qualities that fit.\n"
                      "{music} MP3 audio from any supported link.\n"
                      "{image} Download the thumbnail.\n"
                      "{box} Big files arrive in parts (Telegram limit 2 GB per file).\n"
                      "{chat} Private links: send /cookie first.</blockquote>\n\n"
                      "<b>Supported</b>\n<blockquote>{platforms}</blockquote>"),
        "pick": "{down} <b>{platform}</b> link detected. What do you want?",
        "pick_yt": "{play} <b>{title}</b>\n\nChoose a quality (up to {max_gb} GB):",
        "loading": "{search} Fetching qualities…",
        "best": "Best Quality", "mp3": "MP3", "thumb": "Thumbnail",
        "expired": "This menu expired. Send the link again.",
        "started": "Started",
        "no_thumb": "No thumbnail found for this link.",
        "unsupported": "That link isn't supported.",
        "send_link": "Send me a link.",
        "cmd_na": "That command isn't available. Just send a link.",
    },
}


def t(user_id, key, **fmt) -> str:
    s = T["en"][key]
    if fmt or "{" in s:
        for k in E:
            fmt.setdefault(k, em(k))
        fmt.setdefault("bot", BOT_TITLE)
        s = s.format(**fmt)
    return s


# ---------------- selection buttons (every menu, legacy ones too) ----------------
# Selected ("✅ AVC") -> green + premium tick; unselected ("☑️ AVC", "⚪ x") -> blue.
# Works for single and multi select: each button is styled by its own marker.
_SELECTED = ("✅", "✔️", "✔", "🟢")
_UNSELECTED = ("☑️", "☑", "⚪", "⬜", "🔘")


def style_selection(b) -> None:
    text = getattr(b, "text", "") or ""
    for mark in _SELECTED:
        if text.startswith(mark):
            if b.style in (None, enums.ButtonStyle.DEFAULT, BLUE):
                b.style = GREEN
            # legacy menus have no retry, so only use the icon once Telegram accepted premium emoji
            if premium_on() and state_get("premium_ok") and not b.icon_custom_emoji_id:
                b.text = text[len(mark):].lstrip() or text
                b.icon_custom_emoji_id = E["ok"][1]
            return
    for mark in _UNSELECTED:
        if text.startswith(mark):
            if b.style in (None, enums.ButtonStyle.DEFAULT):
                b.style = BLUE
            if premium_on() and state_get("premium_ok"):
                b.text = text[len(mark):].lstrip() or text
            return


def _install_selection_hook() -> None:
    cls = InlineKeyboardButton
    if getattr(cls, "_selection_hooked", False):
        return
    original = cls.write

    async def write(self, client):
        try:
            style_selection(self)
        except Exception as exc:
            logger.debug(f"[ui] selection style skipped: {exc}")
        return await original(self, client)

    cls.write = write
    cls._selection_hooked = True


_install_selection_hook()
