"""Front door of the bot: welcome screen, link auto-detect, clean command set.

Supported: YouTube, Instagram, TikTok, Snapchat, Pinterest, TeraBox.
Registered at handler group -1 so it runs before every legacy handler.
"""
from __future__ import annotations

import html
import re
from typing import Optional, Tuple
from urllib.parse import urlparse

from pyrogram import filters, enums
from pyrogram.types import (InlineKeyboardButton, InlineKeyboardMarkup,
                            ReplyKeyboardRemove, BotCommand)

from CONFIG.config import Config
from HELPERS.app_instance import get_app
from HELPERS.logger import logger

app = get_app()

# key -> (label, emoji, domain suffixes)
PLATFORMS = {
    "youtube":   ("YouTube",   "▶️", ("youtube.com", "youtu.be", "youtube-nocookie.com")),
    "instagram": ("Instagram", "📸", ("instagram.com", "instagr.am", "ig.me")),
    "tiktok":    ("TikTok",    "🎵", ("tiktok.com", "tiktokv.com")),
    "snapchat":  ("Snapchat",  "👻", ("snapchat.com", "snap.com")),
    "pinterest": ("Pinterest", "📌", ("pinterest.com", "pin.it", "pinterest.co.uk", "pinterest.fr", "pinterest.de", "pinterest.ca", "pinterest.com.au", "pinterest.ph", "pinterest.es", "pinterest.it")),
    "terabox":   ("TeraBox",   "📦", ("terabox.com", "1024terabox.com", "1024tera.com", "terabox.app", "teraboxapp.com",
                                       "freeterabox.com", "mirrobox.com", "nephobox.com", "4funbox.com", "momerybox.com", "tibibox.com")),
}

ALLOWED_USER_COMMANDS = {"start", "help", "cookie", "cookies", "check_cookie", "save_as_cookie", "cookies_from_browser"}

BOT_MENU = [
    ("start", "Welcome"),
    ("help", "How to use"),
    ("cookie", "Private links (cookies)"),
]

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)


def extract_url(text: str) -> Optional[str]:
    m = _URL_RE.search(text or "")
    return m.group(0).rstrip(").,]") if m else None


def detect_platform(text: str) -> Optional[Tuple[str, str, str, str]]:
    """Return (key, label, emoji, url) for a supported link, else None."""
    url = extract_url(text)
    if not url:
        return None
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    for key, (label, emoji, suffixes) in PLATFORMS.items():
        if any(host == s or host.endswith("." + s) for s in suffixes):
            return key, label, emoji, url
    return None


def welcome_text(first_name: str = "") -> str:
    name = html.escape(first_name.strip()) if first_name else "there"
    lines = "\n".join(f"{e} {l}" for l, e, _ in PLATFORMS.values())
    return (
        f"<b>Welcome, {name}!</b>\n\n"
        "Send me a link and I'll download it. No menus, no setup.\n\n"
        f"<blockquote>{lines}</blockquote>\n"
        "Public and private links are supported. For private links, add your cookies first."
    )


def help_text() -> str:
    return (
        "<b>How to use</b>\n\n"
        "1. Copy a link from any supported app\n"
        "2. Send it here\n"
        "3. Wait for the file\n\n"
        "<b>Private links</b>\n"
        "Private Instagram or YouTube links need your login cookies. Use /cookie and follow the steps. "
        "TeraBox private links are not supported."
    )


def _welcome_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📖 How to use", callback_data="ui|help"),
         InlineKeyboardButton("🔒 Private links", callback_data="ui|private")],
    ])


def _is_admin(user_id: int) -> bool:
    try:
        return int(user_id) in Config.ADMIN
    except Exception:
        return False


_menu_set = False


def _ensure_menu(client) -> None:
    global _menu_set
    if _menu_set:
        return
    _menu_set = True
    try:
        client.set_bot_commands([BotCommand(c, d) for c, d in BOT_MENU])
    except Exception as exc:
        logger.warning(f"[ui] set_bot_commands failed: {exc}")


def _clear_reply_keyboard(client, chat_id: int) -> None:
    try:
        m = client.send_message(chat_id, "✨", reply_markup=ReplyKeyboardRemove())
        client.delete_messages(chat_id, m.id)
    except Exception:
        pass


@app.on_message(filters.private & filters.text, group=-1)
def front_door(client, message):
    _ensure_menu(client)
    text = (message.text or "").strip()
    chat_id = message.chat.id
    admin = _is_admin(chat_id)

    if text.startswith("/"):
        cmd = text[1:].split()[0].split("@")[0].lower()
        if cmd in ("start", "help"):
            if cmd == "start":
                _clear_reply_keyboard(client, chat_id)
                client.send_message(chat_id, welcome_text(message.from_user.first_name if message.from_user else ""),
                                    parse_mode=enums.ParseMode.HTML, reply_markup=_welcome_markup())
            else:
                client.send_message(chat_id, help_text(), parse_mode=enums.ParseMode.HTML)
            message.stop_propagation()
        if cmd in ALLOWED_USER_COMMANDS or admin:
            return  # legacy handlers (cookies / admin) continue
        client.send_message(chat_id, "That command isn't available. Just send a link.")
        message.stop_propagation()

    if not _URL_RE.search(text):
        if admin or text in ("",):
            return
        client.send_message(chat_id, "Send me a link from YouTube, Instagram, TikTok, Snapchat, Pinterest or TeraBox.")
        message.stop_propagation()

    found = detect_platform(text)
    if found is None:
        client.send_message(
            chat_id,
            "That link isn't supported.\n\n<blockquote>" +
            "\n".join(f"{e} {l}" for l, e, _ in PLATFORMS.values()) + "</blockquote>",
            parse_mode=enums.ParseMode.HTML)
        message.stop_propagation()
    # supported -> fall through to the download pipeline


@app.on_callback_query(filters.regex(r"^ui\|"), group=-1)
def ui_callbacks(client, cq):
    action = cq.data.split("|", 1)[1]
    if action == "help":
        client.send_message(cq.message.chat.id, help_text(), parse_mode=enums.ParseMode.HTML)
    elif action == "private":
        client.send_message(cq.message.chat.id,
                            "<b>Private links</b>\nUse /cookie to add your login cookies. "
                            "After that, send the private link as usual.", parse_mode=enums.ParseMode.HTML)
    cq.answer()
    cq.stop_propagation()
