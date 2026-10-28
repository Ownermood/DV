"""Group downloads: chosen in the group, done in the member's DM, shown in both.

A group request runs on a "DM twin" (a message in the member's private chat), so every
per-person thing (download lock, rate limit, Cancel, temp folder) is the member's own.
While it runs:
  * the progress card is mirrored into the group (same topic),
  * every file sent to the DM is copied into the group, and that copy goes after 1 minute.
When it ends, the group card goes and the member's /dl message goes 1 minute later.
"""
from __future__ import annotations

import asyncio
import threading
import time

from HELPERS.logger import logger

GROUP_COPY_TTL = 60
_mirrors = {}            # member's DM chat id -> {"chat", "thread", "request", "card", "at"}
_lock = threading.Lock()
MEDIA_METHODS = ("send_video", "send_document", "send_audio", "send_photo", "send_animation",
                 "send_voice", "send_media_group")


def start(dm_chat, group_msg) -> None:
    thread = getattr(group_msg, "message_thread_id", None) if getattr(group_msg, "is_topic_message", False) else None
    with _lock:
        _mirrors[int(dm_chat)] = {"chat": group_msg.chat.id, "thread": thread, "request": group_msg.id,
                                  "card": None, "at": time.time()}


def active(dm_chat):
    with _lock:
        return _mirrors.get(dm_chat) if isinstance(dm_chat, int) else None


def stop(app, dm_chat) -> None:
    """The job ended: the group card goes now, the member's /dl message after 1 minute."""
    with _lock:
        m = _mirrors.pop(int(dm_chat), None)
    if not m:
        return
    if m["card"]:
        _delete(app, m["chat"], [m["card"]])
    delete_later(app, m["chat"], [m["request"]])


def mirror_card(app, dm_chat, text, markup) -> None:
    """Show the same progress card in the group (created on the first update)."""
    m = active(dm_chat)
    if not m:
        return
    from pyrogram import enums
    from HELPERS.progress_ui import edit_later

    def run():
        from HELPERS.progress_ui import strip_premium
        kw = {"parse_mode": enums.ParseMode.HTML, "reply_markup": markup}
        extra = {"message_thread_id": m["thread"]} if m["thread"] else {}
        for body in (text, strip_premium(text)):   # plain emoji if Telegram rejects the premium ones
            try:
                if m["card"]:
                    app.edit_message_text(m["chat"], m["card"], body, **kw)
                else:
                    m["card"] = app.send_message(m["chat"], body, reply_to_message_id=m["request"], **kw, **extra).id
                return
            except Exception as exc:
                if "MESSAGE_NOT_MODIFIED" in str(exc):
                    return
    edit_later(run, m["chat"], m["card"] or f"new{dm_chat}")


def _delete(app, chat, ids) -> None:
    ids = [i for i in ids if i]
    if not ids:
        return
    try:
        app.delete_messages(chat, ids)
    except Exception as exc:
        logger.info(f"[group] delete in {chat} failed: {exc}")


def delete_later(app, chat, ids, after=None) -> None:
    timer = threading.Timer(GROUP_COPY_TTL if after is None else after, _delete, args=(app, chat, ids))
    timer.daemon = True
    timer.start()


def _copy_to_group(app, dm_chat, result, m) -> None:
    extra = {"message_thread_id": m["thread"]} if m["thread"] else {}
    try:
        if isinstance(result, list):
            copies = app.copy_media_group(m["chat"], dm_chat, result[0].id, **extra)
            ids = [c.id for c in copies or []]
        else:
            ids = [app.copy_message(m["chat"], dm_chat, result.id, **extra).id]
        delete_later(app, m["chat"], ids)
    except Exception as exc:
        logger.warning(f"[group] copy to {m['chat']} failed: {exc}")


def _wrap(app, orig):
    def wrapper(*args, **kw):
        result = orig(*args, **kw)
        chat = kw.get("chat_id", args[0] if args else None)
        m = active(chat)
        if m and result and not asyncio.iscoroutine(result):
            threading.Thread(target=_copy_to_group, args=(app, chat, result, m), daemon=True).start()
        return result
    wrapper._group_mirror = True
    return wrapper


def install(app) -> None:
    """Copy files sent to a member's DM into the group while a group job runs (once)."""
    for name in MEDIA_METHODS:
        orig = getattr(app, name, None)
        if orig is not None and not getattr(orig, "_group_mirror", False):
            setattr(app, name, _wrap(app, orig))
