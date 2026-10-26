"""Admin panel: stats, broadcast (copy + pin + live progress), delete broadcast,
users, block/unblock, uncache, premium emoji toggle, start image.

Opened with /admin or the Admin Panel button on /start (admins only).
"""
from __future__ import annotations

import io
import os
import platform
import secrets
import threading
import time
from datetime import datetime

from pyrogram import filters
from pyrogram.errors import FloodWait
from pyrogram.types import InlineKeyboardMarkup

from CONFIG.config import Config
from COMMANDS.ui_kit import btn, em, send, edit, state_get, state_set, premium_on, BLUE, GREEN, RED
from HELPERS.app_instance import get_app
from HELPERS.logger import logger

app = get_app()
_START_TS = time.time()
_MAX_BROADCAST_RECORDS = 5
_DEAD_ERRORS = ("USER_IS_BLOCKED", "INPUT_USER_DEACTIVATED", "USER_DEACTIVATED", "PEER_ID_INVALID", "CHAT_WRITE_FORBIDDEN")

_input = {}          # admin_id -> pending action ("bc", "block", "unblock", "uncache", "startimg", "ckyt", "cktt")
_drafts = {}         # admin_id -> (chat_id, message_id) of the broadcast draft
_bc_lock = threading.Lock()
_bc_running = {"on": False, "stop": False}


def _is_admin(uid) -> bool:
    try:
        return int(uid) in Config.ADMIN
    except Exception:
        return False


# ---------------- data ----------------
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
    users = set(_db_ids("users"))
    users.update(a for a in Config.ADMIN if a > 0)
    return sorted(users)


def blocked_users() -> set:
    return set(_db_ids("blocked_users"))


def _fmt_dur(sec) -> str:
    sec = int(sec)
    d, sec = divmod(sec, 86400)
    h, sec = divmod(sec, 3600)
    m, s = divmod(sec, 60)
    return (f"{d}d " if d else "") + f"{h:02d}h {m:02d}m {s:02d}s"


def _bar(done, total, width=12) -> str:
    frac = done / total if total else 1
    full = int(frac * width)
    return "▰" * full + "▱" * (width - full) + f" {frac * 100:.0f}%"


# ---------------- views ----------------
def _home():
    users = all_users()
    text = (f"{em('crown')} <b>Admin Panel</b>\n\n"
            f"<blockquote>{em('hug')} Users: <b>{len(users)}</b>\n"
            f"{em('bolt')} Uptime: <b>{_fmt_dur(time.time() - _START_TS)}</b>\n"
            f"{em('box')} Max size: <b>{float(Config.MAX_FILE_SIZE_GB):g} GB</b></blockquote>\n"
            f"Choose an option {em('down')}")
    prem = "ON" if premium_on() else "OFF"
    kb = InlineKeyboardMarkup([
        [btn("laptop", "Stats", cb="ap|stats", style=BLUE), btn("chat", "Broadcast", cb="ap|bc", style=GREEN)],
        [btn("trash", "Delete Broadcast", cb="ap|dbl", style=RED), btn("folder", "Users", cb="ap|users", style=BLUE)],
        [btn("ban", "Block", cb="ap|ask|block", style=RED), btn("ok", "Unblock", cb="ap|ask|unblock", style=GREEN)],
        [btn("refresh", "Uncache URL", cb="ap|ask|uncache", style=BLUE), btn("image", "Start Image", cb="ap|ask|startimg", style=BLUE)],
        [btn("play", "YouTube Cookie", cb="ap|ask|ckyt", style=BLUE), btn("music", "TikTok Cookie", cb="ap|ask|cktt", style=BLUE)],
        [btn("stars", f"Premium Emoji: {prem}", cb="ap|prem", style=GREEN if premium_on() else RED)],
        [btn("exit", "Close", cb="ap|close", style=RED)],
    ])
    return text, kb


def _back_kb(extra=None):
    rows = list(extra or [])
    rows.append([btn("back", "Back", cb="ap|home", style=BLUE)])
    return InlineKeyboardMarkup(rows)


def _stats():
    lines = []
    users = all_users()
    blocked = blocked_users()
    try:
        from HELPERS.download_status import active_downloads
        active = len(active_downloads)
    except Exception:
        active = "?"
    lines.append(f"{em('hug')} Total users: <b>{len(users)}</b>")
    lines.append(f"{em('ban')} Blocked: <b>{len(blocked)}</b>")
    lines.append(f"{em('down')} Active downloads: <b>{active}</b>")
    lines.append(f"{em('chat')} Saved broadcasts: <b>{len(state_get('broadcasts', []))}</b>")
    sys_lines = [f"{em('bolt')} Uptime: <b>{_fmt_dur(time.time() - _START_TS)}</b>"]
    try:
        import psutil
        vm = psutil.virtual_memory()
        du = psutil.disk_usage(".")
        sys_lines += [f"{em('laptop')} CPU: <b>{psutil.cpu_percent(interval=0.3):.0f}%</b>",
                      f"{em('box')} RAM: <b>{vm.percent:.0f}%</b> of {vm.total / 1024 ** 3:.1f} GB",
                      f"{em('folder')} Disk free: <b>{du.free / 1024 ** 3:.1f} GB</b> ({100 - du.percent:.0f}%)"]
    except Exception:
        pass
    try:
        import yt_dlp
        sys_lines.append(f"{em('play')} yt-dlp: <b>{yt_dlp.version.__version__}</b>")
    except Exception:
        pass
    sys_lines.append(f"{em('memo')} Python: <b>{platform.python_version()}</b>")
    text = (f"{em('laptop')} <b>Bot Stats</b>\n\n<blockquote>" + "\n".join(lines) + "</blockquote>\n"
            f"<b>System</b>\n<blockquote>" + "\n".join(sys_lines) + "</blockquote>")
    return text, _back_kb([[btn("refresh", "Refresh", cb="ap|stats", style=GREEN)]])


_ASK = {
    "block": "Send the <b>user ID</b> to block.",
    "unblock": "Send the <b>user ID</b> to unblock.",
    "uncache": "Send the <b>URL</b> to remove from cache.",
    "ckyt": ("Send the shared <b>YouTube cookies.txt</b> (Netscape format). Used for every user without their own "
             "cookie: fixes \"Sign in to confirm you're not a bot\". Send <code>off</code> to remove."),
    "cktt": "Send the shared <b>TikTok cookies.txt</b>. Send <code>off</code> to remove.",
    "startimg": "Send a <b>photo</b> for the /start screen, or <code>off</code> to remove it.",
    "bc": ("Send the message to broadcast.\n\n<blockquote>Any type works: text, photo, video, file, "
           "sticker, with premium emoji and buttons. It is copied exactly.</blockquote>"),
}


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
        [[btn("ban", "Cancel", cb="ap|home", style=RED)]])))


def _legacy(client, uid, text, fn_name):
    """Run an existing admin command with a synthetic message."""
    from HELPERS.safe_messeger import fake_message
    from COMMANDS import admin_cmd
    getattr(admin_cmd, fn_name)(client, fake_message(text, uid))


def handle_admin_input(client, message) -> bool:
    """Return True if the message was consumed by a pending admin action."""
    uid = message.chat.id
    action = _input.get(uid)
    if not action or not _is_admin(uid):
        return False
    text = (message.text or "").strip()
    if text.startswith("/"):
        _input.pop(uid, None)  # any command cancels the prompt
        return False
    _input.pop(uid, None)
    back = lambda body: (body, _back_kb())
    try:
        if action == "bc":
            _drafts[uid] = (message.chat.id, message.id)
            send(client, uid, lambda: (f"{em('popcorn')} <b>Preview above.</b> Send it to <b>{len(all_users())}</b> users?",
                                       InlineKeyboardMarkup([
                                           [btn("ok", "Send", cb="ap|bcgo|0", style=GREEN),
                                            btn("plus", "Send + Pin", cb="ap|bcgo|1", style=BLUE)],
                                           [btn("ban", "Cancel", cb="ap|home", style=RED)]])),
                 reply_to_message_id=message.id)
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
        send(client, uid, lambda: back(f"{em('warn')} Failed: <code>{str(exc)[:200]}</code>"))
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
    return bool(m.chat) and m.chat.id in _input


@app.on_message(filters.private & ~filters.text & filters.create(_awaiting_input), group=-1)
def admin_media_input(client, message):
    if handle_admin_input(client, message):
        message.stop_propagation()


# ---------------- broadcast ----------------
def _save_record(rec):
    records = state_get("broadcasts", [])
    records.append(rec)
    state_set("broadcasts", records[-_MAX_BROADCAST_RECORDS:])


def _broadcast(client, admin_id, src, pin):
    from_chat, msg_id = src
    try:
        draft = client.get_messages(from_chat, msg_id)
        markup = getattr(draft, "reply_markup", None)
    except Exception:
        markup = None
    blocked = blocked_users()
    users = [u for u in all_users() if u not in blocked]
    total = len(users)
    ok = dead = fail = 0
    sent = {}
    stop_kb = InlineKeyboardMarkup([[btn("ban", "Stop", cb="ap|bcstop", style=RED)]])
    prog = send(client, admin_id, lambda: (f"{em('chat')} Broadcast starting… 0/{total}", stop_kb))
    last = 0.0
    started = time.time()

    def view(final=False):
        done = ok + dead + fail
        rate = done / max(time.time() - started, 1)
        eta = (total - done) / rate if rate else 0
        head = (f"{em('party')} <b>Broadcast finished</b>" if final and not _bc_running["stop"] else
                f"{em('warn')} <b>Broadcast stopped</b>" if final else f"{em('chat')} <b>Broadcasting…</b>")
        body = (f"{head}\n\n<code>{_bar(done, total)}</code>\n<blockquote>"
                f"{em('ok')} Sent: <b>{ok}</b>\n{em('ban')} Blocked/deleted: <b>{dead}</b>\n"
                f"{em('warn')} Failed: <b>{fail}</b>\n{em('hug')} Total: <b>{total}</b>\n"
                + (f"{em('bolt')} Took: <b>{_fmt_dur(time.time() - started)}</b>" if final else
                   f"{em('bolt')} ETA: <b>{_fmt_dur(eta)}</b>") + "</blockquote>")
        return body, (_back_kb() if final else stop_kb)

    try:
        for uid in users:
            if _bc_running["stop"]:
                break
            for attempt in range(3):
                try:
                    m = client.copy_message(uid, from_chat, msg_id, reply_markup=markup)
                    sent[str(uid)] = m.id
                    ok += 1
                    if pin:
                        try:
                            client.pin_chat_message(uid, m.id, disable_notification=True, both_sides=True)
                        except Exception:
                            pass
                    break
                except FloodWait as fw:
                    time.sleep(int(getattr(fw, "value", 5)) + 1)
                    continue
                except Exception as exc:
                    if any(e in str(exc).upper() for e in _DEAD_ERRORS):
                        dead += 1
                    else:
                        fail += 1
                        logger.info(f"[broadcast] {uid}: {exc}")
                    break
            else:
                fail += 1
            time.sleep(0.04)  # ~25 msg/s, under Telegram's bot limit
            if time.time() - last > 3:
                last = time.time()
                try:
                    edit(client, prog, view)
                except Exception:
                    pass
    finally:
        if sent:
            _save_record({"id": secrets.token_hex(3), "ts": int(time.time()), "pin": bool(pin), "sent": sent})
        _bc_running["on"] = False
        try:
            edit(client, prog, lambda: view(final=True))
        except Exception:
            pass


def _broadcast_list():
    recs = list(reversed(state_get("broadcasts", [])))
    if not recs:
        return f"{em('trash')} <b>No saved broadcasts.</b>", _back_kb()
    rows = []
    for r in recs:
        when = datetime.fromtimestamp(r["ts"]).strftime("%d %b %H:%M")
        rows.append([btn("trash", f"#{r['id']} • {when} • {len(r['sent'])} msgs", cb=f"ap|dbq|{r['id']}", style=RED)])
    return (f"{em('trash')} <b>Delete a broadcast</b>\n\n<blockquote>Removes the message from every user's chat. "
            f"Telegram may refuse messages older than 48h.</blockquote>"), _back_kb(rows)


def _delete_broadcast(client, admin_id, rec_id, prog):
    recs = state_get("broadcasts", [])
    rec = next((r for r in recs if r["id"] == rec_id), None)
    if not rec:
        return
    items = list(rec["sent"].items())
    ok = fail = 0
    last = 0.0
    for uid, mid in items:
        for attempt in range(3):
            try:
                client.delete_messages(int(uid), int(mid))
                ok += 1
                break
            except FloodWait as fw:
                time.sleep(int(getattr(fw, "value", 5)) + 1)
            except Exception:
                fail += 1
                break
        time.sleep(0.04)
        if time.time() - last > 3:
            last = time.time()
            try:
                edit(client, prog, lambda: (f"{em('trash')} Deleting…\n<code>{_bar(ok + fail, len(items))}</code>", None))
            except Exception:
                pass
    state_set("broadcasts", [r for r in state_get("broadcasts", []) if r["id"] != rec_id])
    edit(client, prog, lambda: (f"{em('ok')} <b>Broadcast #{rec_id} deleted</b>\n<blockquote>"
                                f"{em('check')} Deleted: <b>{ok}</b>\n{em('warn')} Failed: <b>{fail}</b></blockquote>",
                                _back_kb()))


# ---------------- callbacks ----------------
@app.on_callback_query(filters.regex(r"^ap\|"), group=-1)
def admin_callbacks(client, cq):
    uid = cq.from_user.id
    if not _is_admin(uid):
        cq.answer("Admins only", show_alert=True)
        cq.stop_propagation()
    parts = cq.data.split("|")
    action = parts[1]
    msg = cq.message
    try:
        if action == "home":
            open_panel(client, uid, msg)
        elif action == "close":
            _input.pop(uid, None)
            msg.delete()
        elif action == "stats":
            edit(client, msg, _stats)
        elif action == "prem":
            state_set("premium", not premium_on())
            open_panel(client, uid, msg)
        elif action == "ask" and len(parts) == 3 and parts[2] in _ASK:
            _ask(client, msg, uid, parts[2])
        elif action == "bc":
            if _bc_running["on"]:
                cq.answer("A broadcast is already running", show_alert=True)
            else:
                _ask(client, msg, uid, "bc")
        elif action == "bcgo" and len(parts) == 3:
            src = _drafts.pop(uid, None)
            with _bc_lock:
                busy = _bc_running["on"]
                if src and not busy:
                    _bc_running.update(on=True, stop=False)
            if not src:
                cq.answer("Draft expired. Start again.", show_alert=True)
            elif busy:
                cq.answer("A broadcast is already running", show_alert=True)
            else:
                msg.delete()
                threading.Thread(target=_broadcast, args=(client, uid, src, parts[2] == "1"), daemon=True).start()
        elif action == "bcstop":
            _bc_running["stop"] = True
            cq.answer("Stopping…")
        elif action == "users":
            users = all_users()
            data = io.BytesIO("\n".join(map(str, users)).encode())
            data.name = "users.txt"
            client.send_document(uid, data, caption=f"📂 {len(users)} users")
        elif action == "dbl":
            edit(client, msg, _broadcast_list)
        elif action == "dbq" and len(parts) == 3:
            edit(client, msg, lambda: (f"{em('warn')} Delete broadcast <b>#{parts[2]}</b> from all chats?",
                                       InlineKeyboardMarkup([[btn("trash", "Yes, delete", cb=f"ap|dbx|{parts[2]}", style=RED),
                                                              btn("back", "No", cb="ap|dbl", style=BLUE)]])))
        elif action == "dbx" and len(parts) == 3:
            edit(client, msg, lambda: (f"{em('trash')} Deleting…", None))
            threading.Thread(target=_delete_broadcast, args=(client, uid, parts[2], msg), daemon=True).start()
    except Exception as exc:
        if "MESSAGE_NOT_MODIFIED" not in str(exc):
            logger.warning(f"[admin] callback {cq.data} failed: {exc}")
    try:
        cq.answer()
    except Exception:
        pass
    cq.stop_propagation()
