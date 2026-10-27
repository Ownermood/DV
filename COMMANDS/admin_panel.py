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
from pyrogram.types import InlineKeyboardMarkup, BotCommand, BotCommandScopeChat

from CONFIG.config import Config
from COMMANDS import activity
from COMMANDS.ui_kit import (btn, em, send, edit, state_get, state_set, premium_on, card, row, heading,
                             small_caps, SEP, BOT_TITLE, BLUE, GREEN, RED)
from HELPERS.app_instance import get_app
from HELPERS.logger import logger

app = get_app()
IST = timezone(timedelta(hours=5, minutes=30))
_START_TS = time.time()
_MAX_BROADCAST_RECORDS = 10
_DEAD_ERRORS = ("USER_IS_BLOCKED", "INPUT_USER_DEACTIVATED", "USER_DEACTIVATED", "PEER_ID_INVALID", "CHAT_WRITE_FORBIDDEN")
OWNERS = {int(a) for a in Config.ADMIN if int(a) > 0}   # from .env, never removable

_input = {}          # admin_id -> pending action
_drafts = {}         # admin_id -> {"chat", "id", "album"}: broadcast waiting for Send
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


def all_users() -> list:
    users = set(_db_users())
    users.update(a for a in Config.ADMIN if a > 0)
    return sorted(users)


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
        from HELPERS.download_status import active_downloads
        return len(active_downloads)
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
    users = _db_users()
    act = activity.snapshot()
    j = _joined(users)
    text = card("crown", "Admin Panel",
                heading("bolt", "Today"),
                [row("hug", "New users", j[0]), row("ok", "Active users", act["active"][0]),
                 row("down", "Downloads", act["dl"][0])],
                [row("folder", "Members", len(users)), row("box", "Downloading now", _running_downloads()),
                 row("laptop", "Uptime", _fmt_dur(time.time() - _START_TS))],
                footer=f"{em('down')} <b>Choose a section:</b>")
    kb = InlineKeyboardMarkup([
        [btn("laptop", "Bot Stats", cb="ap|stats", style=BLUE), btn("hug", "Users Stats", cb="ap|ustats", style=BLUE)],
        [btn("chat", "Broadcast", cb="ap|bc", style=GREEN), btn("trash", "Delete Broadcast", cb="ap|dbl", style=RED)],
        [btn("crown", "Admins", cb="ap|admins", style=GREEN), btn("folder", "Users", cb="ap|sec|users", style=BLUE)],
        [btn("stars", "Settings", cb="ap|sec|settings", style=BLUE)],
        [btn("exit", "Close", cb="ap|close", style=RED)],
    ])
    return text, kb


def _back_kb(extra=None, back="ap|home"):
    rows = list(extra or [])
    rows.append([btn("back", "Back", cb=back, style=RED)])
    return InlineKeyboardMarkup(rows)


def _bot_stats():
    users = _db_users()
    act = activity.snapshot()
    j = _joined(users)
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
    text = card("laptop", "BOT STATS",
                heading("hug", "Users"),
                [row("folder", "Total users", len(users)),
                 row("party", "Joined today / 7d / 30d", f"{j[0]} · {j[1]} · {j[2]}"),
                 row("ok", "Active today / 7d / 30d", f"{act['active'][0]} · {act['active'][1]} · {act['active'][2]}")],
                heading("down", "Downloads"),
                [row("down", "Today", act["dl"][0]), row("down", "Last 7 days", act["dl"][1]),
                 row("down", "Last 30 days", act["dl"][2]), row("medal", "All time", act["dl"][3]),
                 row("box", "Running now", _running_downloads())],
                heading("laptop", "System"), sys_rows,
                footer=_tracking_note(act) + _footer())
    return text, _back_kb([[btn("refresh", "Refresh", cb="ap|stats", style=GREEN),
                            btn("hug", "Users Stats", cb="ap|ustats", style=BLUE)]])


def _users_stats():
    users = _db_users()
    act = activity.snapshot()
    j = _joined(users)
    total = len(users)
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
    return text, _back_kb([[btn("refresh", "Refresh", cb="ap|ustats", style=GREEN),
                            btn("laptop", "Bot Stats", cb="ap|stats", style=BLUE)]])


SECTIONS = {
    "users": ("folder", "Users", "Block or unblock a user, export the user list, clear a cached URL.", [
        ("ban", "Block", "ap|ask|block", RED), ("ok", "Unblock", "ap|ask|unblock", GREEN),
        ("file", "Users List", "ap|users", BLUE), ("refresh", "Uncache URL", "ap|ask|uncache", BLUE)]),
    "settings": ("stars", "Settings", "Start photo, premium emoji and the shared cookies for YouTube / TikTok.", [
        ("image", "Start Image", "ap|ask|startimg", BLUE), ("stars", "Premium Emoji", "ap|prem", GREEN),
        ("play", "YouTube Cookie", "ap|ask|ckyt", BLUE), ("music", "TikTok Cookie", "ap|ask|cktt", BLUE)]),
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
    "ckyt": ("Send the shared <b>YouTube cookies.txt</b> (Netscape format). Used for every user without their own "
             "cookie: fixes \"Sign in to confirm you're not a bot\". Send <code>off</code> to remove."),
    "cktt": "Send the shared <b>TikTok cookies.txt</b>. Send <code>off</code> to remove.",
    "startimg": "Send a <b>photo</b> for the /start screen, or <code>off</code> to remove it.",
    "addadmin": ("Send the new admin as <code>Name user_id</code>\n"
                 "Example: <code>Wifu 8494053059</code>\n\nPremium emojis in the name are kept."),
    "bc": ("Send the message to broadcast.\n\n<blockquote>Any type works: text, photo, video, file, album, "
           "sticker, with premium emojis and buttons. It is copied exactly, without the \"Forwarded from\" tag.</blockquote>"),
}
_ASK_BACK = {"addadmin": "ap|admins", "block": "ap|sec|users", "unblock": "ap|sec|users", "uncache": "ap|sec|users",
             "ckyt": "ap|sec|settings", "cktt": "ap|sec|settings", "startimg": "ap|sec|settings"}


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
        [[btn("back", "Cancel", cb=_ASK_BACK.get(action, "ap|home"), style=RED)]])))


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
    kb = InlineKeyboardMarkup([[btn("laptop", "Open Admin Panel", cb="ap|home", style=GREEN)]])
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
        elif action in ("ckyt", "cktt"):
            send(client, uid, lambda: back(_save_shared_cookie(client, message, text, action)))
    except Exception as exc:
        logger.error(f"[admin] input {action} failed: {exc}")
        send(client, uid, lambda: back(f"{em('warn')} Failed: <code>{escape(str(exc)[:200])}</code>"))
    return True


_COOKIE_FILES = {"ckyt": os.path.join("cookies", "youtube.txt"), "cktt": os.path.join("cookies", "tiktok.txt")}


def _save_shared_cookie(client, message, text, action) -> str:
    dest = _COOKIE_FILES[action]
    if text.lower() == "off":
        if os.path.exists(dest):
            os.remove(dest)
        return f"{em('ok')} Shared cookie removed."
    doc = message.document
    if not doc or (doc.file_size or 0) > 1024 * 1024:
        return f"{em('warn')} Send a cookies <b>.txt</b> file (max 1 MB)."
    os.makedirs("cookies", exist_ok=True)
    tmp = dest + ".upload"
    try:
        client.download_media(message, file_name=tmp)
        with open(tmp, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        rows = [l for l in content.splitlines() if l and not l.startswith("#") and len(l.split("\t")) == 7]
        if not rows:
            return f"{em('warn')} Not a Netscape cookies.txt file."
        os.replace(tmp, dest)  # atomic swap; old file stays if anything above failed
        return f"{em('ok')} Shared cookie saved ({len(rows)} cookies)."
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


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
def _make_draft(client, uid, src):
    """Remember src (or its whole album) and ask Send / Send + Pin / Cancel."""
    gid = getattr(src, "media_group_id", None)
    if gid:
        now = time.time()
        for k in [k for k, ts in _album_parts.items() if now - ts > 3600]:
            _album_parts.pop(k, None)
        _album_parts[gid] = now
    _drafts[uid] = {"chat": src.chat.id, "id": src.id, "album": bool(gid)}
    total = len([u for u in all_users() if u not in blocked_users()])
    send(client, uid, lambda: (card("popcorn", small_caps("Broadcast preview"), [
        row("chat", "Message", "album" if gid else "the message above"),
        row("hug", "Will be sent to", f"{total} users"),
        row("ok", "Forward tag", "no · copied exactly, premium emojis kept")]),
        InlineKeyboardMarkup([[btn("ok", "Send", cb="ap|bcgo|0", style=GREEN),
                               btn("plus", "Send + Pin", cb="ap|bcgo|1", style=BLUE)],
                              [btn("back", "Cancel", cb="ap|home", style=RED)]])),
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
    try:
        draft["msg"] = client.get_messages(draft["chat"], draft["id"])   # for its buttons
    except Exception:
        draft["msg"] = None
    blocked = blocked_users()
    users = [u for u in all_users() if u not in blocked]
    total = len(users)
    stats = {"done": 0, "sent": 0, "failed": 0, "blocked": 0}
    started = time.time()
    rec = {"id": secrets.token_hex(4), "ts": int(started), "pin": bool(pin), "items": [], "sent": 0, "deleted": False,
           "refs": [f"{draft['chat']}:{draft['id']}"], "status": "running"}
    stop_kb = InlineKeyboardMarkup([[btn("warn", "Stop Broadcast", cb="ap|bcstop", style=RED)]])
    prog = send(client, admin_id, lambda: (_bc_card(stats, total, started), stop_kb))
    rec["refs"].append(f"{prog.chat.id}:{prog.id}")
    _save_record(rec)
    last = time.time()
    try:
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
                _save_record(rec)
                try:
                    edit(client, prog, lambda: (_bc_card(stats, total, started), stop_kb))
                except Exception:
                    pass
    finally:
        stopped = _bc_job["stop"]
        rec.update(sent=stats["sent"], status="done")
        _save_record(rec)
        _bc_job["on"] = False
        rows = [[btn("trash", "Delete Broadcast", cb=f"ap|dbq|{rec['id']}", style=RED)]] if rec["items"] else []
        try:
            edit(client, prog, lambda: (_bc_card(stats, total, started, finished=True, stopped=stopped), _back_kb(rows)))
        except Exception:
            pass


def _start_broadcast(cq_or_none, client, uid, pin) -> str:
    draft = _drafts.pop(uid, None)
    with _bc_lock:
        busy = _bc_job["on"]
        if draft and not busy:
            _bc_job.update(on=True, stop=False)
    if not draft:
        return "Draft expired. Start again."
    if busy:
        _drafts[uid] = draft
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
                                btn("back", "Cancel", cb="ap|dbl", style=BLUE)]])
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
                                       btn("back", "No", cb="ap|rml", style=BLUE)]])))
        elif action == "rmx" and arg and _is_owner(uid):
            note = _remove_admin(client, arg)
            text, kb = _remove_admin_view()
            edit(client, msg, lambda: (note + "\n\n" + text, kb))
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
    except Exception as exc:
        if "MESSAGE_NOT_MODIFIED" not in str(exc):
            logger.warning(f"[admin] callback {cq.data} failed: {exc}")
    try:
        cq.answer()
    except Exception:
        pass
    cq.stop_propagation()
