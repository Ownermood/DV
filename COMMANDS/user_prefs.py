"""⚙️ User settings: own thumbnail and default quality, for all platforms or one platform.

Stored per person in the kv store (MongoDB / state file) as prefs:<user id> =
{"thumb": {scope: photo file_id}, "quality": {scope: choice}}, scope = "all" or a platform key.
A platform's own setting beats "all". Works in private chats and in groups (the person who sent the link).
"""
from __future__ import annotations

import hashlib
import os
import threading
import time

from pyrogram import filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from HELPERS.app_instance import get_app
from HELPERS.logger import logger
from COMMANDS.ui_kit import btn, em, send, edit, state_get, state_set, premium_on, BLUE, GREEN

app = get_app()
PLAIN = enums.ButtonStyle.DEFAULT

SCOPES = ("all", "youtube", "instagram", "facebook", "snapchat", "pinterest", "terabox")
QUALITY = {"ask": "Ask every time", "b": "Best", "1080": "1080p", "720": "720p", "480": "480p",
           "360": "360p", "a": "MP3 audio"}
TERABOX_QUALITY = ("ask", "b", "1080", "720", "480")      # TeraBox "Best" = the original file
THUMB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "user_thumbs")  # not users/: that is cleaned after every download
WAIT_TTL = 300
_waiting = {}            # user id -> (scope, since) while the bot waits for a thumbnail photo
_lock = threading.Lock()


# ---------------- storage ----------------
def get(uid) -> dict:
    try:
        return state_get(f"prefs:{int(uid)}", {}) or {}
    except Exception:
        return {}


def _update(uid, part, scope, value) -> None:
    with _lock:
        prefs = get(uid)
        table = dict(prefs.get(part) or {})
        if value is None:
            table.pop(scope, None)
        else:
            table[scope] = value
        prefs[part] = table
        state_set(f"prefs:{int(uid)}", prefs)


def _pick(table, platform):
    table = table or {}
    if platform in table:
        return platform, table[platform]
    return ("all", table["all"]) if "all" in table else (None, None)


def quality(uid, platform):
    """The person's default quality for this platform, or None (= show the menu)."""
    q = _pick(get(uid).get("quality"), platform)[1]
    if platform == "terabox" and q not in TERABOX_QUALITY:
        return None
    return None if q in (None, "ask") else q


def set_quality(uid, scope, q) -> None:
    _update(uid, "quality", scope, None if q in (None, "-") or (scope == "all" and q == "ask") else q)


def set_thumb(uid, scope, file_id) -> None:
    _update(uid, "thumb", scope, file_id)


# ---------------- thumbnail files ----------------
def _make_files(src, thumb, cover) -> None:
    """Telegram thumb: JPEG ≤ 320 px and < 200 KB. Cover: the photo itself, ≤ 1280 px."""
    from PIL import Image
    with Image.open(src) as im:
        im = im.convert("RGB")
        big = im.copy()
        big.thumbnail((1280, 1280))
        big.save(cover, "JPEG", quality=90)
        im.thumbnail((320, 320))
        for q in (90, 80, 70, 60, 50):
            im.save(thumb, "JPEG", quality=q)
            if os.path.getsize(thumb) < 200 * 1024:
                break


def media_for(uid, platform):
    """(thumb path, cover path) of the person's own thumbnail for this platform, or (None, None)."""
    if not uid or not platform:
        return None, None
    scope, fid = _pick(get(uid).get("thumb"), platform)
    if not fid:
        return None, None
    folder = os.path.join(THUMB_DIR, str(int(uid)))
    tag = hashlib.md5(fid.encode()).hexdigest()[:10]
    thumb = os.path.join(folder, f"thumb_{scope}_{tag}.jpg")
    cover = os.path.join(folder, f"cover_{scope}_{tag}.jpg")
    if os.path.exists(thumb) and os.path.exists(cover):
        return thumb, cover
    try:
        os.makedirs(folder, exist_ok=True)
        src = get_app().download_media(fid, file_name=os.path.join(folder, f"src_{tag}.jpg"))
        _make_files(src, thumb, cover)
        os.remove(src)
        return thumb, cover
    except Exception as exc:
        logger.warning(f"[prefs] thumbnail for {uid} failed: {exc}")
        return None, None


def media_for_url(uid, url):
    from COMMANDS.simple_ui import detect_platform
    found = detect_platform(url or "")
    return media_for(uid, found[0]) if found else (None, None)


def has_thumb_for_url(uid, url) -> bool:
    """Own thumbnail set for this link's platform (such videos must not go to / come from the shared cache)."""
    from COMMANDS.simple_ui import detect_platform
    found = detect_platform(url or "")
    return bool(found and _pick(get(uid).get("thumb"), found[0])[1])


def person_of(message):
    """Who asked for the download (the group member, not the group)."""
    pid = getattr(getattr(message, "from_user", None), "id", None)
    if pid and pid > 0:
        return pid
    from HELPERS import cancel
    return cancel.owner(message.chat.id)


# ---------------- screens ----------------
def _label(scope) -> str:
    if scope == "all":
        return "All Platforms"
    from COMMANDS.simple_ui import PLATFORMS
    return PLATFORMS[scope][0]


def _icon(scope) -> str:
    if scope == "all":
        return em("globe")
    from COMMANDS.simple_ui import platform_icon
    return platform_icon(scope)


def _scope_btn(scope, text, cb, style=PLAIN):
    if scope == "all":
        return btn("globe", text, cb=cb, style=style)
    from COMMANDS.simple_ui import PLATFORMS, PLATFORM_EMOJI
    if premium_on():
        return InlineKeyboardButton(text, callback_data=cb, icon_custom_emoji_id=PLATFORM_EMOJI[scope], style=style)
    return InlineKeyboardButton(f"{PLATFORMS[scope][1]} {text}", callback_data=cb, style=style)


def _scope_rows(prefix, table):
    rows, row = [], []
    for s in SCOPES:
        mark = " ✓" if s in (table or {}) else ""
        row.append(_scope_btn(s, _label(s) + mark, f"{prefix}|{s}"))
        if len(row) == 2 or s == "all":
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows


def _summary(table, names=None) -> str:
    if not table:
        return "Not set"
    return " · ".join(f"{_label(s)}" + (f": {names.get(v, v)}" if names else "") for s, v in table.items())


def home_view(uid):
    p = get(uid)
    text = (f"{em('memo')} <b>Your Settings</b>\n"
            "<blockquote>Make the bot work your way. Your settings work in private chat and in groups.</blockquote>\n\n"
            f"{em('image')} <b>Thumbnail</b>\n<blockquote>{_summary(p.get('thumb'))}</blockquote>\n"
            f"{em('play')} <b>Default quality</b>\n<blockquote>{_summary(p.get('quality'), QUALITY)}</blockquote>")
    kb = InlineKeyboardMarkup([[btn("image", "Thumbnail", cb="st|th", style=BLUE),
                                btn("play", "Default Quality", cb="st|q", style=BLUE)],
                               [btn("back", "Back", cb="ui|home", style=PLAIN)]])
    return text, kb


def thumb_view(uid):
    text = (f"{em('image')} <b>Your Thumbnail</b>\n"
            "<blockquote>Your own cover photo on every video I send you.\n"
            "Set one for all platforms, or a different one per platform (it wins over All Platforms).</blockquote>\n\n"
            f"{em('ok')} <b>Choose where it applies</b> (✓ = set)")
    rows = _scope_rows("st|ths", get(uid).get("thumb"))
    rows.append([btn("back", "Back", cb="st|home", style=PLAIN)])
    return text, InlineKeyboardMarkup(rows)


def thumb_scope_view(uid, scope):
    has = scope in (get(uid).get("thumb") or {})
    if has:
        text = (f"{_icon(scope)} <b>Thumbnail · {_label(scope)}</b>\n"
                f"<blockquote>{em('check')} Set. Videos from {_label(scope).replace('All Platforms', 'all platforms')} "
                "get your cover photo.</blockquote>")
        kb = [[btn("image", "View", cb=f"st|thv|{scope}", style=PLAIN),
               btn("refresh", "Change", cb=f"st|thn|{scope}", style=BLUE)],
              [btn("trash", "Remove", cb=f"st|thr|{scope}", style=PLAIN)]]
    else:
        text = (f"{_icon(scope)} <b>Thumbnail · {_label(scope)}</b>\n"
                f"<blockquote>{em('warn')} Not set.</blockquote>")
        kb = [[btn("plus", "Set Thumbnail", cb=f"st|thn|{scope}", style=GREEN)]]
    kb.append([btn("back", "Back", cb="st|th", style=PLAIN)])
    return text, InlineKeyboardMarkup(kb)


def quality_view(uid):
    text = (f"{em('play')} <b>Default Quality</b>\n"
            "<blockquote>Skip the quality menu: your links download straight away in this quality.\n"
            "Not available for a video? I take the next lower one.</blockquote>\n\n"
            f"{em('ok')} <b>Choose where it applies</b> (✓ = set)")
    rows = _scope_rows("st|qs", get(uid).get("quality"))
    rows.append([btn("back", "Back", cb="st|home", style=PLAIN)])
    return text, InlineKeyboardMarkup(rows)


def quality_scope_view(uid, scope):
    table = get(uid).get("quality") or {}
    current = table.get(scope, "-" if scope != "all" else "ask")
    keys = TERABOX_QUALITY if scope == "terabox" else tuple(QUALITY)
    names = dict(QUALITY, b="Original file") if scope == "terabox" else QUALITY
    rows, row = [], []
    if scope != "all":
        follow = QUALITY.get(table.get("all", "ask"), "Ask every time")
        rows.append([btn("globe", f"Same as All Platforms ({follow})", cb=f"st|qset|{scope}|-",
                         style=GREEN if current == "-" else PLAIN)])
    for k in keys:
        row.append(btn("music" if k == "a" else "question" if k == "ask" else "bolt" if k == "b" else "play",
                       names[k], cb=f"st|qset|{scope}|{k}", style=GREEN if current == k else PLAIN))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([btn("back", "Back", cb="st|q", style=PLAIN)])
    shown = names.get(current) if current != "-" else "Same as All Platforms"
    text = (f"{_icon(scope)} <b>Default Quality · {_label(scope)}</b>\n"
            f"<blockquote>{em('check')} Now: <b>{shown}</b></blockquote>")
    return text, InlineKeyboardMarkup(rows)


# ---------------- callbacks ----------------
@app.on_callback_query(filters.regex(r"^st\|"), group=-1)
def settings_callbacks(client, cq):
    parts = cq.data.split("|")
    uid = cq.from_user.id
    msg = cq.message
    action = parts[1] if len(parts) > 1 else "home"
    scope = parts[2] if len(parts) > 2 and parts[2] in SCOPES else None
    try:
        if action == "home":
            _waiting.pop(uid, None)
            edit(client, msg, lambda: home_view(uid))
        elif action == "th":
            _waiting.pop(uid, None)
            edit(client, msg, lambda: thumb_view(uid))
        elif action == "ths" and scope:
            edit(client, msg, lambda: thumb_scope_view(uid, scope))
        elif action == "thn" and scope:
            _waiting[uid] = (scope, time.time())
            edit(client, msg, lambda: (f"{em('image')} <b>Send me a photo now</b>\n"
                                       f"<blockquote>It becomes your thumbnail for <b>{_label(scope)}</b>.\n"
                                       "Square or 16:9 photos look best.</blockquote>",
                                       InlineKeyboardMarkup([[btn("back", "Cancel", cb=f"st|ths|{scope}", style=PLAIN)]])))
        elif action == "thr" and scope:
            set_thumb(uid, scope, None)
            cq.answer("Thumbnail removed")
            edit(client, msg, lambda: thumb_scope_view(uid, scope))
        elif action == "thv" and scope:
            fid = (get(uid).get("thumb") or {}).get(scope)
            if fid:
                client.send_photo(uid, fid, caption=f"Thumbnail · {_label(scope)}")
        elif action == "q":
            edit(client, msg, lambda: quality_view(uid))
        elif action == "qs" and scope:
            edit(client, msg, lambda: quality_scope_view(uid, scope))
        elif action == "qset" and scope and len(parts) == 4 and (parts[3] in QUALITY or parts[3] == "-"):
            set_quality(uid, scope, parts[3])
            cq.answer("Saved")
            edit(client, msg, lambda: quality_scope_view(uid, scope))
    except Exception as exc:
        if "MESSAGE_NOT_MODIFIED" not in str(exc):
            logger.warning(f"[prefs] callback {cq.data} failed: {exc}")
    try:
        cq.answer()
    except Exception:
        pass
    cq.stop_propagation()


# ---------------- the thumbnail photo ----------------
def _wants_photo(_, __, m) -> bool:
    item = _waiting.get(getattr(m.chat, "id", None))
    if item and time.time() - item[1] > WAIT_TTL:
        _waiting.pop(m.chat.id, None)
        return False
    return bool(item)


@app.on_message(filters.private & (filters.photo | filters.document) & filters.create(_wants_photo), group=-1)
def thumb_photo(client, message):
    uid = message.chat.id
    scope = _waiting.get(uid, (None,))[0]
    doc = message.document
    if doc and not (doc.mime_type or "").startswith("image/"):
        send(client, uid, lambda: (f"{em('warn')} <b>Please send a photo</b> (JPG or PNG).", None),
             reply_to_message_id=message.id)
        message.stop_propagation()
    _waiting.pop(uid, None)
    fid = message.photo.file_id if message.photo else doc.file_id
    try:
        folder = os.path.join(THUMB_DIR, str(uid))
        os.makedirs(folder, exist_ok=True)
        src = client.download_media(fid, file_name=os.path.join(folder, "new_thumb_src"))
        _make_files(src, os.path.join(folder, "check_thumb.jpg"), os.path.join(folder, "check_cover.jpg"))
        for name in ("new_thumb_src", "check_thumb.jpg", "check_cover.jpg"):
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass
    except Exception as exc:
        logger.warning(f"[prefs] bad thumbnail from {uid}: {exc}")
        send(client, uid, lambda: (f"{em('warn')} <b>I can't use this image.</b> Send another photo.", None),
             reply_to_message_id=message.id)
        message.stop_propagation()
    set_thumb(uid, scope, fid)
    send(client, uid, lambda: (f"{em('check')} <b>Thumbnail saved for {_label(scope)}!</b> {em('party')}\n"
                               "<blockquote>Your next videos get this cover photo.</blockquote>",
                               InlineKeyboardMarkup([[btn("memo", "Settings", cb="st|home", style=BLUE)]])),
         reply_to_message_id=message.id)
    message.stop_propagation()
