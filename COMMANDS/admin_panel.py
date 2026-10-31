"""Admin panel (VILLAN OTP bot layout): home with today's numbers, sections,
Bot / Users stats with system info, admins (owners add/remove, welcome message),
broadcast (copy without forward tag, albums, pin, live card, stop), delete
broadcast (48h note, live card, /delbc), users, settings.

Owners = ADMIN_IDS from .env. Added admins live in CONFIG/.ui_state.json.
Opened with /admin or the Admin Panel button on /start.
"""
from __future__ import annotations

import io
import os
import platform
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from html import escape

from pyrogram import filters
from pyrogram.errors import FloodWait
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand, BotCommandScopeChat

from CONFIG.config import Config
from COMMANDS import activity
from COMMANDS.ui_kit import (btn, em, send, edit, state_get, state_set, premium_on, card, row, heading,
                             small_caps, SEP, BOT_TITLE, GREEN, RED)
from pyrogram.enums import ButtonStyle

PLAIN = ButtonStyle.DEFAULT  # navigation; GREEN = add / send / working, RED = delete / stop / not working
from HELPERS.app_instance import get_app
from HELPERS.logger import logger

app = get_app()
IST = timezone(timedelta(hours=5, minutes=30))
_START_TS = time.time()
_MAX_BROADCAST_RECORDS = 10
_DEAD_ERRORS = ("USER_IS_BLOCKED", "INPUT_USER_DEACTIVATED", "USER_DEACTIVATED", "PEER_ID_INVALID", "CHAT_WRITE_FORBIDDEN",
                "CHANNEL_PRIVATE", "CHAT_FORBIDDEN", "CHANNEL_INVALID", "USER_BANNED_IN_CHANNEL", "CHAT_ID_INVALID")
OWNERS = {int(a) for a in Config.ADMIN if int(a) > 0}   # from .env, never removable

_input = {}          # admin_id -> pending action
_drafts = {}         # admin_id -> {"chat", "id", "album"}: broadcast waiting for Send (also saved: survives a restart)
_album_parts = {}    # media_group_id -> ts: later parts of an admin's broadcast album (swallowed)
_bc_lock = threading.Lock()
_bc_job = {"on": False, "stop": False}
_rec_lock = threading.Lock()


# ---------------- admins ----------------
def _saved_admins() -> dict:
    return state_get("admins", {}) or {}


def _sync_admins() -> None:
    """Config.ADMIN = owners + added admins (legacy code checks Config.ADMIN)."""
    ids = set(OWNERS) | {int(k) for k in _saved_admins()}
    Config.ADMIN[:] = sorted(ids)


_sync_admins()


def _is_admin(uid) -> bool:
    try:
        return int(uid) in Config.ADMIN
    except Exception:
        return False


def _is_owner(uid) -> bool:
    return int(uid) in OWNERS


ADMIN_MENU = [("start", "Start"), ("help", "What this bot can do"), ("admin", "Open the admin panel")]


def set_admin_menu(client, uid, on=True) -> None:
    try:
        scope = BotCommandScopeChat(int(uid))
        if on:
            client.set_bot_commands([BotCommand(c, d) for c, d in ADMIN_MENU], scope=scope)
        else:
            client.delete_bot_commands(scope=scope)
    except Exception as exc:
        logger.info(f"[admin] menu for {uid} not updated: {exc}")


def ensure_admin_menus(client) -> None:
    for uid in Config.ADMIN:
        set_admin_menu(client, uid, True)


def _admin_name(uid) -> str:
    a = _saved_admins().get(str(uid)) or {}
    return a.get("name_html") or escape(a.get("name") or "") or f"<code>{uid}</code>"


# ---------------- data ----------------
def _db_users() -> dict:
    """{user_id: join timestamp or 0} from the users database."""
    try:
        from DATABASE.firebase_init import db
        items = db.child("bot").child(Config.BOT_NAME_FOR_USERS).child("users").get().each() or []
    except Exception as exc:
        logger.warning(f"[admin] reading users failed: {exc}")
        return {}
    out = {}
    for it in items:
        try:
            k = int(it.key())
            if k == 0:
                continue
            val = it.val() if callable(getattr(it, "val", None)) else None
            out[k] = int((val or {}).get("timestamp") or 0) if isinstance(val, dict) else 0
        except Exception:
            continue
    return out


def _db_ids(node: str) -> list:
    try:
        from DATABASE.firebase_init import db
        items = db.child("bot").child(Config.BOT_NAME_FOR_USERS).child(node).get().each() or []
    except Exception as exc:
        logger.warning(f"[admin] reading {node} failed: {exc}")
        return []
    out = []
    for it in items:
        try:
            k = int(it.key())
            if k != 0:
                out.append(k)
        except Exception:
            continue
    return out


def _user_counts():
    """(total users, [joined today, 7d, 30d]). MongoDB: indexed counts, no full user list in memory."""
    from DATABASE import mongo_db
    if mongo_db.enabled():
        coll = mongo_db.database()["tree"]
        q = {"_id": {"$regex": "^" + re.escape(f"bot/{Config.BOT_NAME_FOR_USERS}/users/")}}
        today = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0)
        starts = [today.timestamp(), (today - timedelta(days=6)).timestamp(), (today - timedelta(days=29)).timestamp()]
        return coll.count_documents(q), [coll.count_documents({**q, "v.timestamp": {"$gte": t}}) for t in starts]
    users = _db_users()
    return len(users), _joined(users)


def all_users() -> list:
    """People (private chats). Group ids an old version saved in the user list are left out."""
    users = {u for u in _db_users() if u > 0}
    users.update(a for a in Config.ADMIN if a > 0)
    return sorted(users)


def all_targets() -> tuple:
    """(users, groups) a broadcast goes to, blocked ones left out."""
    from COMMANDS.groups_store import all_groups
    blocked = blocked_users()   # once, not once per user
    groups = set(all_groups()) | {u for u in _db_users() if u < 0}   # old versions saved groups as users
    return [u for u in all_users() if u not in blocked], sorted(g for g in groups if g not in blocked)


def blocked_users() -> set:
    return set(_db_ids("blocked_users"))


def _fmt_dur(sec) -> str:
    sec = int(max(0, sec))
    d, sec = divmod(sec, 86400)
    h, sec = divmod(sec, 3600)
    m, s = divmod(sec, 60)
    if d:
        return f"{d}d {h}h {m}m"
    return f"{h}h {m}m" if h else f"{m}m {s}s" if m else f"{s}s"


def _bar(pct: float, width: int = 10) -> str:
    filled = min(width, int(round(pct / 100 * width)))
    return "▰" * filled + "▱" * (width - filled)


def _joined(users: dict) -> list:
    """Joined today / last 7 days / last 30 days (IST)."""
    today = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0)
    starts = [today.timestamp(), (today - timedelta(days=6)).timestamp(), (today - timedelta(days=29)).timestamp()]
    return [sum(1 for ts in users.values() if ts and ts >= s) for s in starts]


def _running_downloads():
    try:
        from HELPERS.download_status import active_downloads, active_downloads_lock
        with active_downloads_lock:   # finished downloads stay in the dict as False
            return sum(1 for on in active_downloads.values() if on)
    except Exception:
        return "—"


def _footer() -> str:
    return f"{em('bolt')} <i>Live · updated {datetime.now(IST).strftime('%d %b, %H:%M:%S')} IST</i>"


def _tracking_note(act) -> str:
    since = act.get("since")
    when = datetime.fromtimestamp(since, IST).strftime("%d %b %Y") if since else "now"
    return f"{em('memo')} <i>Activity and downloads are counted since {when}.</i>\n"


# ---------------- screens ----------------
def _home():
    n_users, j = _user_counts()
    act = activity.snapshot()
    text = card("crown", "Admin Panel",
                heading("bolt", "Today"),
                [row("hug", "New users", j[0]), row("ok", "Active users", act["active"][0]),
                 row("down", "Downloads", act["dl"][0])],
                [row("folder", "Members", n_users), row("box", "Downloading now", _running_downloads()),
                 row("laptop", "Uptime", _fmt_dur(time.time() - _START_TS))],
                footer=f"{em('down')} <b>Choose a section:</b>")
    kb = InlineKeyboardMarkup([
        [btn("laptop", "Bot Stats", cb="ap|stats", style=PLAIN), btn("hug", "Users Stats", cb="ap|ustats", style=PLAIN)],
        [btn("chat", "Broadcast", cb="ap|bc", style=PLAIN), btn("trash", "Delete Broadcast", cb="ap|dbl", style=PLAIN)],
        [btn("crown", "Admins", cb="ap|admins", style=PLAIN), btn("folder", "Users", cb="ap|sec|users", style=PLAIN)],
        [btn("lips", "Cookie Manager", cb="ap|ckm", style=PLAIN), btn("stars", "Settings", cb="ap|sec|settings", style=PLAIN)],
        [btn("exit", "Close", cb="ap|close", style=PLAIN)],
    ])
    return text, kb


def _back_kb(extra=None, back="ap|home"):
    rows = list(extra or [])
    rows.append([btn("back", "Back", cb=back, style=PLAIN)])
    return InlineKeyboardMarkup(rows)


def _bot_stats():
    n_users, j = _user_counts()
    act = activity.snapshot()
    sys_rows = [row("bolt", "Uptime", _fmt_dur(time.time() - _START_TS))]
    try:
        import psutil
        vm = psutil.virtual_memory()
        du = psutil.disk_usage(".")
        sys_rows += [row("laptop", "CPU", f"{psutil.cpu_percent(interval=0.3):.0f}%"),
                     row("box", "RAM", f"{vm.percent:.0f}% of {vm.total / 1024 ** 3:.1f} GB"),
                     row("folder", "Disk free", f"{du.free / 1024 ** 3:.1f} GB ({100 - du.percent:.0f}%)")]
    except Exception:
        pass
    try:
        import yt_dlp
        sys_rows.append(row("play", "yt-dlp", yt_dlp.version.__version__))
    except Exception:
        pass
    sys_rows.append(row("memo", "Python", platform.python_version()))
    try:  # same checks the health watcher DMs about
        from HELPERS.alerts import health_snapshot
        for name, ok in health_snapshot().items():
            sys_rows.append(row("ok" if ok else "warn", name, "OK" if ok else "DOWN"))
    except Exception:
        pass
    text = card("laptop", "BOT STATS",
                heading("hug", "Users"),
                [row("folder", "Total users", n_users),
                 row("party", "Joined today / 7d / 30d", f"{j[0]} · {j[1]} · {j[2]}"),
                 row("ok", "Active today / 7d / 30d", f"{act['active'][0]} · {act['active'][1]} · {act['active'][2]}")],
                heading("down", "Downloads"),
                [row("down", "Today", act["dl"][0]), row("down", "Last 7 days", act["dl"][1]),
                 row("down", "Last 30 days", act["dl"][2]), row("medal", "All time", act["dl"][3]),
                 row("box", "Running now", _running_downloads())],
                heading("laptop", "System"), sys_rows,
                footer=_tracking_note(act) + _footer())
    return text, _back_kb([[btn("refresh", "Refresh", cb="ap|stats", style=PLAIN),
                            btn("hug", "Users Stats", cb="ap|ustats", style=PLAIN)]])


def _users_stats():
    total, j = _user_counts()
    act = activity.snapshot()
    blocked = len(blocked_users())

    def pct(n):
        return f"{n} · {n * 100 / total:.0f}%" if total else str(n)
    text = card("hug", "USER STATS",
                [row("folder", "Total users", total), row("crown", "Admins", len(Config.ADMIN)),
                 row("ban", "Blocked", blocked)],
                heading("party", "New users"),
                [row("party", "Today", j[0]), row("party", "Last 7 days", j[1]), row("party", "Last 30 days", j[2])],
                heading("ok", "Activity"),
                [row("ok", "Active 24h", pct(act["active"][0])), row("ok", "Active 7 days", pct(act["active"][1])),
                 row("ok", "Active 30 days", pct(act["active"][2])),
                 row("warn", "Inactive 7 days+", max(0, act["tracked"] - act["active"][1])),
                 row("question", "No activity yet", max(0, total - act["tracked"]))],
                footer=_tracking_note(act) + _footer())
    return text, _back_kb([[btn("refresh", "Refresh", cb="ap|ustats", style=PLAIN),
                            btn("laptop", "Bot Stats", cb="ap|stats", style=PLAIN)]])


SECTIONS = {
    "users": ("folder", "Users", "Block or unblock a user, export the user list, clear a cached URL.", [
        ("ban", "Block", "ap|ask|block", RED), ("ok", "Unblock", "ap|ask|unblock", GREEN),
        ("file", "Users List", "ap|users", PLAIN), ("refresh", "Uncache URL", "ap|ask|uncache", PLAIN)]),
    "settings": ("stars", "Settings", "Start photo and premium emoji. Cookies: Admin Panel → Cookie Manager.", [
        ("image", "Start Image", "ap|ask|startimg", PLAIN), ("stars", "Premium Emoji", "ap|prem", GREEN),
        ("lips", "Cookie Manager", "ap|ckm", PLAIN)]),
}


def _section(key):
    icon, title, info, items = SECTIONS[key]
    rows, r = [], []
    for ic, label, cb, style in items:
        if cb == "ap|prem":
            label = f"Premium Emoji: {'ON' if premium_on() else 'OFF'}"
            style = GREEN if premium_on() else RED
        r.append(btn(ic, label, cb=cb, style=style))
        if len(r) == 2:
            rows.append(r)
            r = []
    if r:
        rows.append(r)
    return card(icon, title.upper(), [info]), _back_kb(rows)


def _admins_view(uid):
    saved = _saved_admins()
    owners = [f"{em('crown')} Owner · <code>{o}</code>" for o in sorted(OWNERS)]
    added = [f"{em('hug')} {_admin_name(k)} · <code>{k}</code>" for k in saved if int(k) not in OWNERS]
    rows = []
    if _is_owner(uid):
        rows.append([btn("plus", "Add Admin", cb="ap|ask|addadmin", style=GREEN),
                     btn("minus", "Remove Admin", cb="ap|rml", style=RED)])
    text = card("crown", "BOT ADMINS",
                heading("crown", f"Owners · {len(owners)}"), owners,
                heading("hug", f"Admins · {len(added)}"), added or ["<i>No added admins yet.</i>"],
                footer=f"{em('memo')} <i>Owners come from ADMIN_IDS in .env. Only owners can add or remove admins.</i>")
    return text, _back_kb(rows)


def _remove_admin_view():
    saved = {k: v for k, v in _saved_admins().items() if int(k) not in OWNERS}
    rows = [[btn("trash", f"Remove {v.get('name') or k}"[:40], cb=f"ap|rmq|{k}", style=RED)] for k, v in saved.items()]
    lines = [f"{em('hug')} {_admin_name(k)} · <code>{k}</code>" for k in saved]
    return card("minus", "REMOVE ADMIN", lines or ["<i>No added admins.</i>"]), _back_kb(rows, back="ap|admins")


_ASK = {
    "block": "Send the <b>user ID</b> to block.",
    "unblock": "Send the <b>user ID</b> to unblock.",
    "uncache": "Send the <b>URL</b> to remove from cache.",
    "startimg": "Send a <b>photo</b> for the /start screen, or <code>off</code> to remove it.",
    "addadmin": ("Send the new admin as <code>Name user_id</code>\n"
                 "Example: <code>Wifu 8494053059</code>\n\nPremium emojis in the name are kept."),
    "bc": ("Send the message to broadcast.\n\n<blockquote>Any type works: text, photo, video, file, album, "
           "sticker, with premium emojis and buttons. It is copied exactly, without the \"Forwarded from\" tag.</blockquote>"),
}
_ASK_BACK = {"addadmin": "ap|admins", "block": "ap|sec|users", "unblock": "ap|sec|users", "uncache": "ap|sec|users",
             "startimg": "ap|sec|settings"}


def open_panel(client, chat_id, msg=None):
    _input.pop(chat_id, None)
    if msg is not None:
        try:
            return edit(client, msg, _home)
        except Exception as exc:
            if "MESSAGE_NOT_MODIFIED" in str(exc):
                return None
            # photo /start message: send a fresh panel instead
    return send(client, chat_id, _home)


# ---------------- input from admin ----------------
def _ask(client, msg, uid, action):
    _input[uid] = action
    edit(client, msg, lambda: (f"{em('memo')} {_ASK[action]}", InlineKeyboardMarkup(
        [[btn("back", "Cancel", cb=_ASK_BACK.get(action, "ap|home"), style=PLAIN)]])))


def _legacy(client, uid, text, fn_name):
    """Run an existing admin command with a synthetic message."""
    from HELPERS.safe_messeger import fake_message
    from COMMANDS import admin_cmd
    getattr(admin_cmd, fn_name)(client, fake_message(text, uid))


def _add_admin(client, text, html_text, uid):
    """'Name user_id' (any order, name optional). html_text keeps premium emojis. Returns the result card."""
    text = (text or "").strip()
    parts = text.split()
    ids = [p for p in parts if p.lstrip("-").isdigit()]
    if len(ids) != 1:
        return f"{em('warn')} Send it as <code>Name user_id</code>, e.g. <code>Wifu 8494053059</code>"
    new_id = int(ids[0])
    if new_id in OWNERS:
        return f"{em('warn')} <code>{new_id}</code> is already an owner."
    name = " ".join(p for p in parts if p != ids[0]).strip()[:40] or None
    name_html = None
    if name:
        html_text = html_text or escape(text)
        # pyrogram writes custom emoji as <tg-emoji emoji-id="..">x</emoji>: make the tags match
        html_text = re.sub(r'<tg-emoji emoji-id="(\d+)">', r'<emoji id="\1">', html_text)
        name_html = re.sub(rf"(?<![\w>]){re.escape(ids[0])}(?!\w)", "", html_text, count=1).strip() or escape(name)
    saved = _saved_admins()
    saved[str(new_id)] = {"name": name, "name_html": name_html, "by": uid, "ts": int(time.time())}
    state_set("admins", saved)
    _sync_admins()
    set_admin_menu(client, new_id, True)
    notified = _welcome_admin(client, new_id, name_html)
    return card("crown", "ADMIN ADDED", [
        row("hug", "Name", name_html or "—"), row("memo", "User ID", f"<code>{new_id}</code>"),
        row("ok" if notified else "warn", "Notified", "yes" if notified else "no · ask them to /start the bot first")])


def _welcome_admin(client, uid, name_html) -> bool:
    hello = f"{em('crown')} <b>Hey {name_html}!</b>\n" if name_html else ""
    text = (f"{em('stars')} <b>NEW ADMIN</b>\n{SEP}\n{hello}"
            f"<blockquote>𝙶𝚕𝚊𝚍 𝚝𝚘 𝚑𝚊𝚟𝚎 𝚢𝚘𝚞 𝚊𝚜 𝚊𝚗 𝙰𝚍𝚖𝚒𝚗 𝚘𝚏 {BOT_TITLE} | 𝚆𝚎𝚕𝚌𝚘𝚖𝚎 𝚝𝚘 𝚝𝚑𝚎 𝙵𝚊𝚖𝚒𝚕𝚢 ♡ {em('love')}</blockquote>\n"
            f"<blockquote>{em('bolt')} Tap the button below or send /admin to open the Admin Panel.\n"
            f"{em('stars')} It also shows on /start.</blockquote>")
    kb = InlineKeyboardMarkup([[btn("laptop", "Open Admin Panel", cb="ap|home", style=PLAIN)]])
    try:
        send(client, uid, lambda: (text, kb))
        return True
    except Exception as exc:
        logger.info(f"[admin] could not welcome {uid}: {exc}")
        return False


def _remove_admin(client, target) -> str:
    saved = _saved_admins()
    old = saved.pop(str(target), None)
    if old is None:
        return f"{em('warn')} Already removed."
    state_set("admins", saved)
    _sync_admins()
    set_admin_menu(client, target, False)
    try:
        client.send_message(int(target), "ℹ️ You are no longer an admin of this bot.")
    except Exception:
        pass
    return f"{em('trash')} <b>{old.get('name_html') or escape(old.get('name') or '')}</b> <code>{target}</code> removed."


def handle_admin_input(client, message) -> bool:
    """Return True if the message was consumed by a pending admin action."""
    uid = message.chat.id
    gid = getattr(message, "media_group_id", None)
    if gid and gid in _album_parts:
        return True                      # later part of an album that is already the broadcast draft
    action = _input.get(uid)
    if not action or not _is_admin(uid):
        return False
    text = (message.text or "").strip()
    if text.startswith("/"):
        _input.pop(uid, None)  # any command cancels the prompt
        return False
    if action.startswith("ckadd:") or action.startswith("ckrep:"):
        _cookie_input(client, message, uid, action)
        return True
    _input.pop(uid, None)
    back = lambda body: (body, _back_kb(back=_ASK_BACK.get(action, "ap|home")))
    try:
        if action == "bc":
            _make_draft(client, uid, message)
        elif action == "addadmin":
            if not _is_owner(uid):
                send(client, uid, lambda: back(f"{em('ban')} Only owners can add admins."))
            else:
                card_text = _add_admin(client, message.text, getattr(message.text, "html", None), uid)
                send(client, uid, lambda: back(card_text))
        elif action in ("block", "unblock"):
            if not text.lstrip("-").isdigit():
                send(client, uid, lambda: back(f"{em('warn')} Not a valid user ID."))
            else:
                _legacy(client, uid, f"/{action}_user {text}", f"{action}_user")
        elif action == "uncache":
            _legacy(client, uid, f"/uncache {text}", "uncache_command")
        elif action == "startimg":
            if message.photo:
                state_set("start_image", message.photo.file_id)
                send(client, uid, lambda: back(f"{em('ok')} Start image saved."))
            elif text.lower() == "off":
                state_set("start_image", "")
                send(client, uid, lambda: back(f"{em('ok')} Start image removed."))
            else:
                send(client, uid, lambda: back(f"{em('warn')} Send a photo or <code>off</code>."))
    except Exception as exc:
        logger.error(f"[admin] input {action} failed: {exc}")
        err = escape(str(exc)[:200])  # the lambda may run after the except block
        send(client, uid, lambda: back(f"{em('warn')} Failed: <code>{err}</code>"))
    return True


# ---------------- Cookie Manager (like the OSINT bot's API Manager) ----------------
from COMMANDS import cookie_store as cs  # noqa: E402


def _ck_status(s) -> str:
    if not s["on"]:
        return "OFF"
    if s.get("exp") and s["exp"] < time.time():
        return "expired"
    return "not working" if s.get("down") else "working"


def _platform_btn(svc, text, cb):
    """Button with the platform's own premium logo (YouTube, Instagram … not one shared icon)."""
    from COMMANDS.ui_kit import premium_on
    label, key, cid, _ = cs.SERVICES[svc]
    if cid and premium_on():
        return InlineKeyboardButton(text, callback_data=cb, icon_custom_emoji_id=cid, style=PLAIN)
    return btn(key, text, cb=cb, style=PLAIN)


def _ckm_view():
    rows, lines = [], []
    from COMMANDS.simple_ui import platform_icon
    for svc, (label, key, cid, _need) in cs.SERVICES.items():
        sl = cs.slots(svc)
        live = sum(1 for s in sl if cs.usable(s))
        lines.append(f"{platform_icon(svc)} <b>{label}:</b> {live} working / {len(sl)} added")   # real platform logo
        b = _platform_btn(svc, f"{label} · {live}/{len(sl)}", f"ap|cks|{svc}")
        if rows and len(rows[-1]) < 2:   # two platforms side by side
            rows[-1].append(b)
        else:
            rows.append([b])
    rows.append([btn("search", "Test All Now", cb="ap|ckchk", style=PLAIN)])
    text = card("lips", "COOKIE MANAGER", lines,
                [f"{em('bolt')} Rotation: downloads take turns over every working cookie; a failing one falls through to the next.",
                 f"{em('warn')} 5 failures in a row → that cookie is skipped and the Error Log group gets an alert.",
                 f"{em('laptop')} Every {cs.CHECK_EVERY_HOURS}h every cookie is tested; the Error Log group hears about dead ones "
                 "and about any that expire within a day."],
                footer=f"{em('down')} <b>Tap a platform</b> (working / added)")
    return text, _back_kb(rows)


CK_PAGE = 10   # cookies per page in the list (30-50 fit in a few pages)


def _cks_view(svc, page=0):
    label, key, _cid, need = cs.SERVICES[svc]
    sl = cs.slots(svc)
    nxt = cs.next_in_rotation(svc)
    now = time.time()
    working = sum(1 for s in sl if cs.usable(s))
    off = sum(1 for s in sl if not s["on"])
    expired = sum(1 for s in sl if s["on"] and s.get("exp") and s["exp"] < now)
    dead = len(sl) - working - off - expired

    def _d(ts):
        return datetime.fromtimestamp(ts, IST).strftime("%d %b") if ts else "never"
    pages = max(1, -(-len(sl) // CK_PAGE))
    page = max(0, min(page, pages - 1))
    first = page * CK_PAGE
    lines = [f"{em('ok')} <b>{working}</b> working · {em('warn')} <b>{dead}</b> not working · "
             f"⏳ <b>{expired}</b> expired · ⚪ <b>{off}</b> off  (of {len(sl)}/{cs.MAX_SLOTS})"]
    lines += [f"{'✅' if cs.usable(s) else '⚪' if not s['on'] else '❌'} <b>Cookie-{n}</b> {_ck_status(s)}"
              + (" · <b>NEXT</b>" if s["id"] == nxt else "")
              + f" · ok {_d(s.get('last_ok'))} · {cs.exp_text(s.get('exp'))}"
              for n, s in enumerate(sl[first:first + CK_PAGE], first + 1)] if sl else ["<i>No cookie yet. Tap Add Cookie-1.</i>"]
    rows, r = [], []
    for n, s in enumerate(sl[first:first + CK_PAGE], first + 1):
        r.append(btn("ok" if cs.usable(s) else "warn", f"C-{n}", cb=f"ap|ckv|{svc}|{s['id']}",
                     style=GREEN if cs.usable(s) else RED))
        if len(r) == 5:
            rows.append(r)
            r = []
    if r:
        rows.append(r)
    if pages > 1:
        rows.append([btn("back", "Prev", cb=f"ap|cks|{svc}|{(page - 1) % pages}", style=PLAIN),
                     btn("memo", f"{page + 1}/{pages}", cb=f"ap|cks|{svc}|{page}", style=PLAIN),
                     btn("next", "Next", cb=f"ap|cks|{svc}|{(page + 1) % pages}", style=PLAIN)])
    if len(sl) < cs.MAX_SLOTS:
        rows.append([btn("plus", f"Add Cookies (Cookie-{len(sl) + 1}…)", cb=f"ap|cka|{svc}", style=GREEN)])
    if sl:
        rows.append([btn("search", f"Test All {label}", cb=f"ap|ckst|{svc}", style=PLAIN)]
                    + ([btn("trash", f"Remove {dead + expired} Dead", cb=f"ap|ckrd|{svc}", style=RED)] if dead + expired else []))
    text = card(key, f"{label.upper()} COOKIES", lines,
                [f"{em('bolt')} Rotation: each download starts at the NEXT cookie, then tries the others.",
                 f"{em('memo')} Logged-in cookies.txt (login cookie: <code>{' / '.join(need) or 'any'}</code>). Send many files at once.",
                 f"{em('lips')} Use spare accounts, not your main one."])
    return text, _back_kb(rows, back="ap|ckm")


def _ckv_view(svc, sid):
    sl = cs.slots(svc)
    n, s = next(((i, x) for i, x in enumerate(sl, 1) if x["id"] == sid), (None, None))
    if s is None:
        return _cks_view(svc)
    label = cs.SERVICES[svc][0]
    added = datetime.fromtimestamp(s.get("added", 0), IST).strftime("%d %b %Y")
    text = card(cs.SERVICES[svc][1], f"{label.upper()} · COOKIE-{n}", [
        row("ok" if cs.usable(s) else "warn", "Status", _ck_status(s)),
        row("bolt", "Order", f"{n} of {len(sl)}"),
        row("laptop", "Expiry", cs.exp_text(s.get("exp"))),
        row("warn", "Failures in a row", s.get("fails", 0)),
        row("memo", "Added", added)])
    kb = [[btn("ok" if s["on"] else "ban", f"Cookie-{n}: {'ON' if s['on'] else 'OFF'} (tap to turn {'OFF' if s['on'] else 'ON'})",
               cb=f"ap|ckt|{svc}|{sid}", style=GREEN if s["on"] else RED)],
          [btn("search", "Test Now", cb=f"ap|cktest|{svc}|{sid}", style=PLAIN),
           btn("refresh", "Replace", cb=f"ap|ckr|{svc}|{sid}", style=PLAIN)],
          [btn("trash", "Delete", cb=f"ap|ckd|{svc}|{sid}", style=RED)]]
    return text, _back_kb(kb, back=f"ap|cks|{svc}")


_CK_SITE = {"youtube": "youtube.com", "instagram": "instagram.com", "facebook": "facebook.com",
            "terabox": "1024terabox.com (or terabox.com)"}


def _cookie_input(client, message, uid, action):
    """Files sent after Add Cookies (many at once, album or one by one) or Replace."""
    kind, svc, *rest = action.split(":")
    text = (message.text or "").strip().lower()
    done_kb = InlineKeyboardMarkup([[btn("ok", "Done", cb=f"ap|cks|{svc}", style=GREEN)]])
    if text in ("done", "stop", "cancel"):
        _input.pop(uid, None)
        send(client, uid, lambda: _cks_view(svc))
        return
    doc = message.document
    if not doc:
        send(client, uid, lambda: (f"{em('warn')} Send <b>cookies.txt</b> files, or tap Done.", done_kb))
        return
    name = escape((doc.file_name or "file")[:40])
    if (doc.file_size or 0) > 1024 * 1024:
        send(client, uid, lambda: (f"{em('warn')} {name}: too big (max 1 MB).", done_kb))
        return
    tmp = os.path.join(cs.ROOT, f".upload_{uid}_{message.id}")
    os.makedirs(cs.ROOT, exist_ok=True)
    try:
        client.download_media(message, file_name=tmp)
        with open(tmp, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    ok, msg, n = cs.add(svc, content, replace_id=rest[0] if kind == "ckrep" and rest else None)
    label = cs.SERVICES[svc][0]
    if ok:
        exp = next((s.get("exp") for s in cs.slots(svc)[n - 1:n]), 0)
        line = f"{em('ok')} {label} Cookie-{n} {'replaced' if kind == 'ckrep' else 'saved'} · {cs.exp_text(exp)}"
    else:
        line = f"{em('warn')} {name}: {escape(msg)}"
    count = len(cs.slots(svc))
    full = count >= cs.MAX_SLOTS
    list_btn = btn("folder", f"{label} Cookies ({count})", cb=f"ap|cks|{svc}", style=PLAIN)
    if kind == "ckrep" or full:
        _input.pop(uid, None)
        note = f"\n{em('memo')} {cs.MAX_SLOTS} cookies reached." if full and kind != "ckrep" else ""
        kb = InlineKeyboardMarkup([[list_btn]])
    else:  # still waiting: the next file becomes the next cookie
        note = (f"\n\n{em('plus')} <b>Add Cookie-{count + 1}?</b> Just send the next .txt file now."
                if ok else f"\n\n{em('plus')} Send another .txt file, or tap Done.")
        kb = InlineKeyboardMarkup([[btn("ok", "Done", cb=f"ap|cks|{svc}", style=GREEN), list_btn]])
    sent = send(client, uid, lambda: (line + (f"\n{em('search')} Testing…" if ok else "") + note, kb))
    if ok and sent:  # test right away, so a dead cookie is caught now
        sid = cs.slots(svc)[n - 1]["id"]

        def run():
            good, why = cs.test_slot(svc, sid)
            edit(client, sent, lambda: (line + f"\n{em('ok' if good else 'warn')} Test: {escape(why)}" + note, kb))
        threading.Thread(target=run, daemon=True).start()


def _awaiting_input(_, __, m) -> bool:
    if not m.chat:
        return False
    gid = getattr(m, "media_group_id", None)
    return m.chat.id in _input or bool(gid and gid in _album_parts)


@app.on_message(filters.private & ~filters.text & filters.create(_awaiting_input), group=-1)
def admin_media_input(client, message):
    if handle_admin_input(client, message):
        message.stop_propagation()


# ---------------- broadcast ----------------
def _put_draft(uid, draft) -> None:
    _drafts[uid] = draft
    try:
        state_set(f"bc_draft:{uid}", draft)
    except Exception as exc:
        logger.warning(f"[broadcast] draft not saved: {exc}")


def _take_draft(uid):
    draft = _drafts.pop(uid, None)
    try:
        draft = draft or state_get(f"bc_draft:{uid}")
        state_set(f"bc_draft:{uid}", None)
    except Exception:
        pass
    return draft or None


def _make_draft(client, uid, src):
    """Remember src (or its whole album) and ask Send / Send + Pin / Cancel."""
    gid = getattr(src, "media_group_id", None)
    if gid:
        now = time.time()
        for k in [k for k, ts in _album_parts.items() if now - ts > 3600]:
            _album_parts.pop(k, None)
        _album_parts[gid] = now
    _put_draft(uid, {"chat": src.chat.id, "id": src.id, "album": bool(gid)})
    users, groups = all_targets()
    send(client, uid, lambda: (card("popcorn", small_caps("Broadcast preview"), [
        row("chat", "Message", "album" if gid else "the message above"),
        row("hug", "Will be sent to", f"{len(users)} users · {len(groups)} groups"),
        row("ok", "Forward tag", "no · copied exactly, premium emojis kept")]),
        InlineKeyboardMarkup([[btn("ok", "Send", cb="ap|bcgo|0", style=GREEN),
                               btn("plus", "Send + Pin", cb="ap|bcgo|1", style=PLAIN)],
                              [btn("back", "Cancel", cb="ap|home", style=PLAIN)]])),
         reply_to_message_id=src.id)


def _bc_card(stats, total, started, finished=False, stopped=False) -> str:
    done = stats["done"]
    pct = (done * 100 / total) if total else 100
    elapsed = time.time() - started
    if finished:
        title, icon = (small_caps("Broadcast stopped"), "warn") if stopped else (small_caps("Broadcast done"), "party")
    else:
        title, icon = small_caps("Broadcast running..."), "bolt"
    lines = [f"{em(icon)} <b>{title}</b>", "",
             f"{em('bolt')} <b>{small_caps('Done')}:</b> {done}/{total}",
             f"{em('ok')} <b>{small_caps('Sent')}:</b> {stats['sent']} | {em('warn')} <b>{small_caps('Failed')}:</b> {stats['failed']}"]
    if stats["blocked"]:
        lines.append(f"{em('ban')} <b>{small_caps('Blocked bot')}:</b> {stats['blocked']}")
    lines.append(f"{_bar(pct)} <b>{pct:.0f}%</b>")
    if finished:
        lines.append(f"{em('laptop')} <b>{small_caps('Time')}:</b> {_fmt_dur(elapsed)}")
    elif done:
        eta = elapsed / done * (total - done)
        lines.append(f"{em('laptop')} <b>{small_caps('Time')}:</b> {_fmt_dur(elapsed)} | <b>{small_caps('ETA')}:</b> {_fmt_dur(eta)}")
    return "<blockquote>" + "\n".join(lines) + "</blockquote>"


def _records() -> list:
    """Saved broadcasts; records from the old panel ({"sent": {uid: mid}}) are converted."""
    out = []
    for r in state_get("broadcasts", []) or []:
        if isinstance(r.get("sent"), dict):
            r = {"id": r.get("id"), "ts": r.get("ts", 0), "pin": r.get("pin", False), "deleted": False, "status": "done",
                 "items": [[int(u), [int(m)]] for u, m in r["sent"].items()], "sent": len(r["sent"]), "refs": []}
        out.append(r)
    return out


def _save_record(rec) -> None:
    with _rec_lock:
        recs = [r for r in _records() if r.get("id") != rec["id"]]
        recs.append(rec)
        state_set("broadcasts", recs[-_MAX_BROADCAST_RECORDS:])


def _copy(client, uid, draft):
    if draft["album"]:
        res = client.copy_media_group(uid, draft["chat"], draft["id"])
        return [m.id for m in res or []]
    src = draft.get("msg")
    m = client.copy_message(uid, draft["chat"], draft["id"],
                            reply_markup=getattr(src, "reply_markup", None) if src else None)
    return [m.id] if getattr(m, "id", None) else []


def _broadcast(client, admin_id, draft, pin):
    stats = {"done": 0, "sent": 0, "failed": 0, "blocked": 0}
    started = time.time()
    rec = {"id": secrets.token_hex(4), "ts": int(started), "pin": bool(pin), "items": [], "sent": 0, "deleted": False,
           "refs": [f"{draft['chat']}:{draft['id']}"], "status": "running"}
    stop_kb = InlineKeyboardMarkup([[btn("warn", "Stop Broadcast", cb="ap|bcstop", style=RED)]])
    prog, total, last = None, 0, time.time()
    # everything inside try: a crash before the loop used to leave "already running" set until a restart
    try:
        try:
            draft["msg"] = client.get_messages(draft["chat"], draft["id"])   # for its buttons
        except Exception:
            draft["msg"] = None
        people, groups = all_targets()
        users = people + groups   # groups too (pinned there only where the bot may pin)
        total = len(users)
        prog = send(client, admin_id, lambda: (_bc_card(stats, total, started), stop_kb))
        if prog is not None:
            rec["refs"].append(f"{prog.chat.id}:{prog.id}")
        try:
            _save_record(rec)
        except Exception as exc:
            logger.warning(f"[broadcast] record not saved: {exc}")
        for uid in users:
            if _bc_job["stop"]:
                break
            for attempt in range(3):
                try:
                    mids = _copy(client, uid, draft)
                    if mids:
                        rec["items"].append([uid, mids])
                        if _is_admin(uid):
                            rec["refs"].extend(f"{uid}:{m}" for m in mids)
                        if pin:
                            try:
                                client.pin_chat_message(uid, mids[0], disable_notification=True, both_sides=True)
                            except Exception:
                                pass
                    stats["sent"] += 1
                    break
                except FloodWait as fw:
                    time.sleep(int(getattr(fw, "value", 5)) + 1)
                    continue
                except Exception as exc:
                    stats["failed"] += 1
                    if any(e in str(exc).upper() for e in _DEAD_ERRORS):
                        stats["blocked"] += 1
                        if uid < 0:   # the bot was removed from this group / can't write there
                            from COMMANDS.groups_store import forget
                            forget(uid)
                    else:
                        logger.info(f"[broadcast] {uid}: {exc}")
                    break
            else:
                stats["failed"] += 1
            stats["done"] += 1
            time.sleep(0.05)  # ~20 msg/s, under Telegram's bot limit
            if time.time() - last >= 3:   # live card every ~3 s; saved too, so a restart loses nothing
                last = time.time()
                rec["sent"] = stats["sent"]
                try:
                    _save_record(rec)
                    if prog is not None:
                        edit(client, prog, lambda: (_bc_card(stats, total, started), stop_kb))
                except Exception:
                    pass
    except Exception as exc:
        logger.error(f"[broadcast] stopped by an error: {exc}")
        err = escape(str(exc)[:300])
        try:
            send(client, admin_id, lambda: (f"{em('warn')} <b>Broadcast stopped by an error.</b>\n"
                                            f"<blockquote><code>{err}</code></blockquote>", None))
        except Exception:
            pass
    finally:
        stopped = _bc_job["stop"]
        _bc_job["on"] = False   # first: nothing below can leave the flag stuck
        rec.update(sent=stats["sent"], status="done")
        try:
            _save_record(rec)
        except Exception as exc:
            logger.warning(f"[broadcast] record not saved: {exc}")
        rows = [[btn("trash", "Delete Broadcast", cb=f"ap|dbq|{rec['id']}", style=RED)]] if rec["items"] else []
        try:
            done_card = lambda: (_bc_card(stats, total, started, finished=True, stopped=stopped), _back_kb(rows))
            edit(client, prog, done_card) if prog is not None else send(client, admin_id, done_card)
        except Exception:
            pass


def _start_broadcast(cq_or_none, client, uid, pin) -> str:
    draft = _take_draft(uid)
    with _bc_lock:
        busy = _bc_job["on"]
        if draft and not busy:
            _bc_job.update(on=True, stop=False)
    if not draft:
        return "Draft expired. Start again."
    if busy:
        _put_draft(uid, draft)
        return "A broadcast is already running."
    threading.Thread(target=_broadcast, args=(client, uid, draft, pin), daemon=True).start()
    return ""


# ---------------- delete broadcast ----------------
def _when(ts) -> str:
    return datetime.fromtimestamp(ts, IST).strftime("%d %b %I:%M %p") + " IST"


def _broadcast_list():
    recs = [r for r in reversed(_records()) if not r.get("deleted")][:5]
    if not recs:
        return card("trash", small_caps("Recent broadcasts"), ["<i>No broadcast to delete.</i>"]), _back_kb()
    rows = [[btn("trash", f"Delete · {_when(r['ts'])[:-4]} · {r.get('sent', 0)} users", cb=f"ap|dbq|{r['id']}", style=RED)]
            for r in recs]
    return card("trash", small_caps("Recent broadcasts"), ["<i>Times in IST. Tip: reply /delbc to a broadcast.</i>"]), _back_kb(rows)


def _delete_confirm(rec):
    old = time.time() - rec["ts"] > 48 * 3600
    warn = ("⚠️ <b>Older than 48 hours:</b> Telegram will most likely refuse; nothing is lost by trying."
            if old else "<i>Telegram lets a bot delete its messages only within 48 hours.</i>")
    text = card("warn", small_caps("Delete broadcast?"), [
        row("laptop", "Sent", _when(rec["ts"])),
        row("hug", "Removed from", f"{len(rec.get('items') or [])} chats"), warn])
    kb = InlineKeyboardMarkup([[btn("trash", "Confirm Delete", cb=f"ap|dbx|{rec['id']}", style=RED),
                                btn("back", "Cancel", cb="ap|dbl", style=PLAIN)]])
    return text, kb


def _del_card(title, done, total, deleted, failed, note=""):
    pct = (done * 100 / total) if total else 100
    return ("<blockquote>" + "\n".join([
        f"{em('warn')} <b>{title}</b>", "",
        f"{em('bolt')} <b>{small_caps('Done')}:</b> {done}/{total}",
        f"{em('ok')} <b>{small_caps('Deleted')}:</b> {deleted} | {em('warn')} <b>{small_caps('Failed')}:</b> {failed}",
        f"{_bar(pct)} <b>{pct:.0f}%</b>"]) + "</blockquote>" + (f"\n{note}" if note else ""))


def _claim_delete(rec_id):
    """Mark a broadcast deleted (once): two taps can't start two deletions."""
    with _rec_lock:
        recs = _records()
        rec = next((r for r in recs if r.get("id") == rec_id), None)
        if not rec or rec.get("deleted") or rec.get("status") == "running":
            return None
        rec["deleted"] = True
        state_set("broadcasts", recs)
        return rec


def _delete_broadcast(client, rec, prog):
    items = rec.get("items") or []
    total, stats = len(items), {"done": 0, "deleted": 0, "failed": 0}
    title = small_caps("Deleting broadcast...")
    last = time.time()
    for uid, mids in items:
        for attempt in range(2):
            try:
                client.delete_messages(int(uid), [int(m) for m in mids])
                stats["deleted"] += 1
                break
            except FloodWait as fw:
                time.sleep(int(getattr(fw, "value", 5)) + 1)
            except Exception:          # too old (48h), already deleted by the user, bot blocked ...
                stats["failed"] += 1
                break
        stats["done"] += 1
        time.sleep(0.04)
        if time.time() - last >= 3:
            last = time.time()
            try:
                edit(client, prog, lambda: (_del_card(title, stats["done"], total, stats["deleted"], stats["failed"]), None))
            except Exception:
                pass
    note = ("<i>Failed = older than 48 hours, already deleted by the user, or the user blocked the bot.</i>"
            if stats["failed"] else "")
    edit(client, prog, lambda: (_del_card(small_caps("Broadcast deleted"), stats["done"], total, stats["deleted"],
                                          stats["failed"], note), _back_kb()))


def _find_broadcast(chat_id, message_id):
    key = f"{chat_id}:{message_id}"
    for r in reversed(_records()):
        if key in (r.get("refs") or []):
            return r
        for uid, mids in r.get("items") or []:
            if int(uid) == int(chat_id) and int(message_id) in [int(m) for m in mids]:
                return r
    return None


# ---------------- admin commands (/broadcast /delbc /delbroadcast /admins /addadmin /removeadmin) ----------------
def handle_command(client, message, cmd) -> bool:
    """Commands typed by an admin. Returns True when handled."""
    uid = message.chat.id
    reply = getattr(message, "reply_to_message", None)
    if cmd == "broadcast":
        if not reply:
            send(client, uid, lambda: (f"{em('memo')} Reply <code>/broadcast</code> to the message you want to send, "
                                       f"or use Admin Panel → Broadcast.", None))
        else:
            _make_draft(client, uid, reply)
        return True
    if cmd == "delbc":
        rec = _find_broadcast(uid, reply.id) if reply else None
        if not reply:
            send(client, uid, lambda: (f"{em('memo')} Reply <code>/delbc</code> to the broadcast message you want to delete.", None))
        elif not rec:
            send(client, uid, lambda: (f"{em('warn')} This message is not a saved broadcast.", None))
        elif rec.get("deleted"):
            send(client, uid, lambda: (f"{em('ok')} This broadcast was already deleted.", None))
        else:
            send(client, uid, lambda: _delete_confirm(rec))
        return True
    if cmd == "delbroadcast":
        send(client, uid, _broadcast_list)
        return True
    if cmd == "admins":
        send(client, uid, lambda: _admins_view(uid))
        return True
    if cmd == "addadmin":
        if not _is_owner(uid):
            send(client, uid, lambda: (f"{em('ban')} Only owners can add admins.", None))
        elif len((message.text or "").split()) > 1:
            html_full = getattr(message.text, "html", None) or escape(message.text)
            html_args = html_full.split(maxsplit=1)[1] if len(html_full.split(maxsplit=1)) > 1 else ""
            card_text = _add_admin(client, message.text.split(maxsplit=1)[1], html_args, uid)
            send(client, uid, lambda: (card_text, _back_kb(back="ap|admins")))
        else:
            _input[uid] = "addadmin"
            send(client, uid, lambda: (f"{em('memo')} {_ASK['addadmin']}", None))
        return True
    if cmd == "removeadmin":
        if not _is_owner(uid):
            send(client, uid, lambda: (f"{em('ban')} Only owners can remove admins.", None))
        else:
            send(client, uid, _remove_admin_view)
        return True
    return False


# ---------------- callbacks ----------------
class _Done(Exception):
    """Screen already handled inside a branch."""

@app.on_callback_query(filters.regex(r"^ap\|"), group=-1)
def admin_callbacks(client, cq):
    uid = cq.from_user.id
    if not _is_admin(uid):
        cq.answer("Admins only", show_alert=True)
        cq.stop_propagation()
    parts = cq.data.split("|")
    action = parts[1]
    arg = parts[2] if len(parts) > 2 else None
    msg = cq.message
    try:
        if action == "home":
            open_panel(client, uid, msg)
        elif action == "close":
            _input.pop(uid, None)
            msg.delete()
        elif action == "stats":
            cq.answer("Loading…")
            edit(client, msg, _bot_stats)
        elif action == "ustats":
            cq.answer("Loading…")
            edit(client, msg, _users_stats)
        elif action == "sec" and arg in SECTIONS:
            _input.pop(uid, None)
            edit(client, msg, lambda: _section(arg))
        elif action == "admins":
            _input.pop(uid, None)
            edit(client, msg, lambda: _admins_view(uid))
        elif action == "prem":
            state_set("premium", not premium_on())
            edit(client, msg, lambda: _section("settings"))
        elif action == "ask" and arg in _ASK:
            if arg == "addadmin" and not _is_owner(uid):
                cq.answer("Only owners can add admins.", show_alert=True)
            else:
                _ask(client, msg, uid, arg)
        elif action == "rml":
            if _is_owner(uid):
                edit(client, msg, _remove_admin_view)
            else:
                cq.answer("Only owners can remove admins.", show_alert=True)
        elif action == "rmq" and arg and _is_owner(uid):
            edit(client, msg, lambda: (card("warn", "REMOVE THIS ADMIN?", [
                row("hug", "Name", _admin_name(arg)), row("memo", "User ID", f"<code>{arg}</code>")]),
                InlineKeyboardMarkup([[btn("trash", "Yes, Remove", cb=f"ap|rmx|{arg}", style=RED),
                                       btn("back", "No", cb="ap|rml", style=PLAIN)]])))
        elif action == "rmx" and arg and _is_owner(uid):
            note = _remove_admin(client, arg)
            text, kb = _remove_admin_view()
            edit(client, msg, lambda: (note + "\n\n" + text, kb))
        elif action == "ckm":
            _input.pop(uid, None)
            edit(client, msg, _ckm_view)
        elif action == "cks" and arg in cs.SERVICES:
            _input.pop(uid, None)
            page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
            edit(client, msg, lambda: _cks_view(arg, page))
        elif action == "ckst" and arg in cs.SERVICES:   # test every cookie of one platform
            cq.answer("Testing…")
            label = cs.SERVICES[arg][0]
            edit(client, msg, lambda: (f"{em('search')} <b>Testing all {label} cookies…</b>", None))

            def run_svc(svc=arg):
                lines = cs.check_all(alert=False, services=[svc])
                good = sum(1 for l in lines if l.startswith("✅"))
                text, kb = _cks_view(svc)
                edit(client, msg, lambda: (f"{em('search')} <b>Test done: {good} of {len(lines)} working.</b>\n\n" + text, kb))
            threading.Thread(target=run_svc, daemon=True).start()
        elif action == "ckrd" and arg in cs.SERVICES:
            edit(client, msg, lambda: (f"{em('warn')} <b>Delete every {cs.SERVICES[arg][0]} cookie that is not working or expired?</b>",
                                       InlineKeyboardMarkup([[btn("trash", "Yes, Remove", cb=f"ap|ckrdx|{arg}", style=RED),
                                                              btn("back", "No", cb=f"ap|cks|{arg}", style=PLAIN)]])))
        elif action == "ckrdx" and arg in cs.SERVICES:
            n = cs.remove_dead(arg)
            cq.answer(f"Removed {n}")
            edit(client, msg, lambda: _cks_view(arg))
        elif action in ("ckv", "ckt", "ckd", "ckdx", "ckr", "cktest") and arg in cs.SERVICES and len(parts) == 4:
            sid = parts[3]
            if action == "ckt":
                cs.toggle(arg, sid)
            elif action == "ckdx":
                cs.delete(arg, sid)
                cq.answer("Deleted")
                edit(client, msg, lambda: _cks_view(arg))
                raise _Done
            elif action == "ckd":
                edit(client, msg, lambda: (f"{em('warn')} <b>Delete this {cs.SERVICES[arg][0]} cookie?</b>",
                                           InlineKeyboardMarkup([[btn("trash", "Yes, Delete", cb=f"ap|ckdx|{arg}|{sid}", style=RED),
                                                                  btn("back", "No", cb=f"ap|ckv|{arg}|{sid}", style=PLAIN)]])))
                raise _Done
            elif action == "ckr":
                _input[uid] = f"ckrep:{arg}:{sid}"
                edit(client, msg, lambda: (f"{em('refresh')} Send the new <b>{cs.SERVICES[arg][0]} cookies.txt</b> for this slot.",
                                           InlineKeyboardMarkup([[btn("back", "Cancel", cb=f"ap|ckv|{arg}|{sid}", style=PLAIN)]])))
                raise _Done
            elif action == "cktest":  # live test can take ~20 s: answer now, show the result on the card
                cq.answer("Testing…")
                edit(client, msg, lambda: (f"{em('search')} <b>Testing {cs.SERVICES[arg][0]} cookie…</b>", None))

                def run(svc=arg, slot=sid):
                    ok, why = cs.test_slot(svc, slot)
                    text, kb = _ckv_view(svc, slot)
                    edit(client, msg, lambda: (f"{em('ok' if ok else 'warn')} <b>Test: {escape(why)}</b>\n\n" + text, kb))
                threading.Thread(target=run, daemon=True).start()
                raise _Done
            edit(client, msg, lambda: _ckv_view(arg, sid))
        elif action == "cka" and arg in cs.SERVICES:
            if len(cs.slots(arg)) >= cs.MAX_SLOTS:
                cq.answer(f"Max {cs.MAX_SLOTS} cookies. Delete one first.", show_alert=True)
            else:
                _input[uid] = f"ckadd:{arg}"
                label = cs.SERVICES[arg][0]
                nxt = len(cs.slots(arg)) + 1
                edit(client, msg, lambda: (card("plus", f"ADD {label.upper()} COOKIE-{nxt}", [
                    f"1. New Incognito window → log in to {_CK_SITE.get(arg, label)} (spare account)",
                    "2. Get cookies.txt LOCALLY → <b>Export</b>",
                    "3. Close the Incognito window (don't log out)",
                    f"4. Send the <b>.txt file here</b>"],
                    footer=f"{em('ok')} After it is saved you can add Cookie-{nxt + 1} right away."),
                    InlineKeyboardMarkup([[btn("back", "Cancel", cb=f"ap|cks|{arg}", style=PLAIN)]])))
        elif action == "ckchk":
            cq.answer("Testing all cookies…")
            edit(client, msg, lambda: (f"{em('search')} <b>Testing all cookies…</b>", None))

            def run_all():
                lines = cs.check_all(alert=False)
                edit(client, msg, lambda: (card("search", "COOKIE TEST", lines or ["<i>No cookies added.</i>"]),
                                           _back_kb(back="ap|ckm")))
            threading.Thread(target=run_all, daemon=True).start()
        elif action == "bc":
            if _bc_job["on"]:
                cq.answer("A broadcast is already running", show_alert=True)
            else:
                _ask(client, msg, uid, "bc")
        elif action == "bcgo" and arg in ("0", "1"):
            err = _start_broadcast(cq, client, uid, arg == "1")
            if err:
                cq.answer(err, show_alert=True)
            else:
                cq.answer("Broadcast started")
                try:
                    msg.delete()
                except Exception:
                    pass
        elif action == "bcstop":
            _bc_job["stop"] = True
            cq.answer("Stopping… users already sent keep the message.")
        elif action == "users":
            users = all_users()
            data = io.BytesIO("\n".join(map(str, users)).encode())
            data.name = "users.txt"
            client.send_document(uid, data, caption=f"📂 {len(users)} users")
        elif action == "dbl":
            edit(client, msg, _broadcast_list)
        elif action == "dbq" and arg:
            rec = next((r for r in _records() if r.get("id") == arg), None)
            if not rec or rec.get("deleted"):
                cq.answer("Already deleted or not found.", show_alert=True)
            elif rec.get("status") == "running":
                cq.answer("Still sending. Stop it first.", show_alert=True)
            else:
                edit(client, msg, lambda: _delete_confirm(rec))
        elif action == "dbx" and arg:
            rec = _claim_delete(arg)
            if not rec:
                cq.answer("Already deleted or not found.", show_alert=True)
            else:
                cq.answer("Deleting…")
                edit(client, msg, lambda: (_del_card(small_caps("Deleting broadcast..."), 0, len(rec.get("items") or []), 0, 0), None))
                threading.Thread(target=_delete_broadcast, args=(client, rec, msg), daemon=True).start()
    except _Done:
        pass
    except Exception as exc:
        if "MESSAGE_NOT_MODIFIED" not in str(exc):
            logger.warning(f"[admin] callback {cq.data} failed: {exc}")
    try:
        cq.answer()
    except Exception:
        pass
    cq.stop_propagation()
