"""Clean groups: when a requested file arrives, only the file stays.

group_door() tracks the member's /dl message. Every media send that replies to it then
  * goes out without the reply (that message is about to go),
  * carries an invisible mention of the member in the caption (they still get notified),
  * and after the first file is sent, the member's /dl message is deleted.
The progress card is removed by the download pipeline itself.
"""
from __future__ import annotations

import asyncio
import re
import threading
import time

from HELPERS.logger import logger

TTL = 6 * 3600
_tracked = {}            # (chat id, message id) -> {"user": id, "at": ts, "deleted": bool}
_lock = threading.Lock()
MEDIA_METHODS = ("send_video", "send_document", "send_audio", "send_photo", "send_animation",
                 "send_voice", "send_media_group", "send_paid_media", "copy_message", "copy_media_group")
_QUOTE_START = re.compile(r"\s*<blockquote[^>]*>")
_TAGS = re.compile(r"<[^>]+>")


def track(message) -> None:
    """A group member asked for a download with this message."""
    chat = getattr(getattr(message, "chat", None), "id", None)
    user = getattr(getattr(message, "from_user", None), "id", None)
    if not chat or chat > 0 or not user:
        return
    now = time.time()
    with _lock:
        for k in [k for k, v in _tracked.items() if now - v["at"] > TTL]:
            _tracked.pop(k, None)
        _tracked[(chat, message.id)] = {"user": user, "at": now, "deleted": False}


def hidden_mention(caption, user_id, parse_mode=None):
    """Caption with an invisible mention of user_id (zero-width link): notifies, shows nothing."""
    from pyrogram import enums
    if parse_mode == enums.ParseMode.DISABLED:
        return caption
    caption = caption or ""
    if len(_TAGS.sub("", caption)) > 1000:  # no room left under the 1024 caption limit
        return caption
    tag = f'<a href="tg://user?id={int(user_id)}">​</a>'
    m = _QUOTE_START.match(caption)
    return caption[:m.end()] + tag + caption[m.end():] if m else tag + caption


def _reply_id(kw):
    rp = kw.get("reply_parameters")
    return kw.get("reply_to_message_id") or getattr(rp, "message_id", None)


def _wrap(app, name, orig):
    def wrapper(*args, **kw):
        chat = kw.get("chat_id", args[0] if args else None)
        rid = _reply_id(kw)
        with _lock:
            item = _tracked.get((chat, rid)) if isinstance(chat, int) and rid else None
        if not item:
            return orig(*args, **kw)
        kw.pop("reply_to_message_id", None)
        kw.pop("reply_parameters", None)
        if "caption" in kw and name not in ("send_media_group", "copy_media_group"):
            kw["caption"] = hidden_mention(kw["caption"], item["user"], kw.get("parse_mode"))
        elif name == "send_media_group":
            media = kw.get("media", args[1] if len(args) > 1 else None) or []
            if media and hasattr(media[0], "caption"):
                media[0].caption = hidden_mention(media[0].caption, item["user"], getattr(media[0], "parse_mode", None))
        result = orig(*args, **kw)
        if not asyncio.iscoroutine(result) and result:
            with _lock:
                first = not item["deleted"]
                item["deleted"] = True
            if first:  # the file is there: the member's /dl message goes
                threading.Thread(target=_delete, args=(app, chat, rid), daemon=True).start()
        return result
    wrapper._group_clean = True
    return wrapper


def _delete(app, chat, msg_id) -> None:
    try:
        app.delete_messages(chat, msg_id)
    except Exception as exc:
        logger.info(f"[group] could not delete /dl message {msg_id} in {chat}: {exc}")


def install(app) -> None:
    """Wrap the client's media send methods (once)."""
    for name in MEDIA_METHODS:
        orig = getattr(app, name, None)
        if orig is not None and not getattr(orig, "_group_clean", False):
            setattr(app, name, _wrap(app, name, orig))
