"""Cookie Manager store (like the OSINT bot's API Manager DB-1…DB-6).

Up to 10 cookies per platform in cookies/<platform>/<id>.txt, metadata in
CONFIG/.ui_state.json ("cookie_slots"). Downloads use the first ON, working
cookie and rotate to the next one on a login error. 5 failures in a row mark a
cookie down and DM the owners. A watcher checks expiry dates (read from the
cookies.txt itself) and YouTube cookies live every 6 hours.
"""
from __future__ import annotations

import os
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from HELPERS.logger import logger

IST = timezone(timedelta(hours=5, minutes=30))
ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cookies")
MAX_SLOTS = 10
FAIL_ALERT_AT = 5
CHECK_EVERY_HOURS = 6

# platform -> (label, ui_kit emoji key, premium logo id, login cookies that must be present)
SERVICES = {
    "youtube": ("YouTube", "play", "5355235592844095825", ("SAPISID", "__Secure-3PSID", "SID", "LOGIN_INFO")),
    "instagram": ("Instagram", "image", "5355097780228470775", ("sessionid",)),
    "facebook": ("Facebook", "chat", "5355254460635428635", ("c_user", "xs")),
    "terabox": ("TeraBox", "box", "5976766782763962762", ("ndus",)),
}

_lock = threading.RLock()


# ---------------- storage ----------------
def _all() -> dict:
    from COMMANDS.ui_kit import state_get
    return state_get("cookie_slots", {}) or {}


def _save(data) -> None:
    from COMMANDS.ui_kit import state_set
    state_set("cookie_slots", data)


def slots(service) -> list:
    with _lock:
        _migrate(service)
        return [dict(s) for s in _all().get(service, [])]


def _path(service, slot_id) -> str:
    return os.path.join(ROOT, service, f"{slot_id}.txt")


def _migrate(service) -> None:
    """A cookie uploaded with the old single-cookie Settings button becomes Cookie-1."""
    legacy = os.path.join(ROOT, f"{service}.txt")
    data = _all()
    if service in data or not os.path.exists(legacy):
        return
    try:
        with open(legacy, "r", encoding="utf-8", errors="ignore") as f:
            ok, _msg, _slot = _add(service, f.read(), data)
        if ok:
            _save(data)
            os.replace(legacy, legacy + ".migrated")
    except Exception as exc:
        logger.warning(f"[cookies] migrate {service} failed: {exc}")


# ---------------- parsing ----------------
def parse(content: str) -> dict:
    """{cookie name: (value, expiry ts)} from a Netscape cookies.txt."""
    out = {}
    for line in (content or "").splitlines():
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        if not line or line.startswith("#"):
            continue
        parts = line.rstrip("\n").split("\t")
        if len(parts) != 7:
            continue
        try:
            exp = int(float(parts[4] or 0))
        except ValueError:
            exp = 0
        out[parts[5]] = (parts[6], exp)
    return out


def _login_expiry(service, cookies: dict) -> int:
    """Earliest expiry of the login cookies (0 = session cookie / unknown)."""
    exps = [cookies[n][1] for n in SERVICES[service][3] if n in cookies and cookies[n][1] > 0]
    return min(exps) if exps else 0


def _fingerprint(service, cookies: dict) -> str:
    import hashlib
    key = "|".join(cookies[n][0] for n in SERVICES[service][3] if n in cookies)
    return hashlib.sha256(key.encode()).hexdigest()[:16]


# Only these sites' lines are kept from an uploaded cookies.txt ("Export All" files carry every site's logins)
DOMAINS = {
    "youtube": ("youtube.com", "google.com"),
    "instagram": ("instagram.com",),
    "facebook": ("facebook.com", "fb.com"),
    "terabox": ("terabox", "1024tera", "teraboxapp", "freeterabox", "4funbox", "mirrobox", "nephobox", "momerybox",
                "tibibox", "teraboxlink", "terasharelink", "1024terabox"),
}


def only_site(service, content: str) -> str:
    """Header + the cookie lines of this platform's domains."""
    keep = ["# Netscape HTTP Cookie File"]
    doms = DOMAINS.get(service, ())
    for line in (content or "").splitlines():
        host = line[len("#HttpOnly_"):] if line.startswith("#HttpOnly_") else line
        if not host or host.startswith("#"):
            continue
        h = host.split("\t", 1)[0].lstrip(".").lower()
        # TeraBox has many mirror domains (terabox.app, 1024terabox.com …): substring match there
        if any((d in h) if service == "terabox" else (h == d or h.endswith("." + d)) for d in doms):
            keep.append(line)
    return "\n".join(keep) + "\n"


# ---------------- add / change ----------------
def _add(service, content, data, replace_id=None):
    content = only_site(service, content)
    cookies = parse(content)
    if not cookies:
        return False, "Not a Netscape cookies.txt file.", None
    need = SERVICES[service][3]
    if not any(n in cookies for n in need):
        return False, f"No login cookie ({' / '.join(need)}). Log in to {SERVICES[service][0]} first, then export.", None
    exp = _login_expiry(service, cookies)
    if exp and exp < time.time():
        return False, "This cookie is already expired. Log in again and export a fresh one.", None
    fp = _fingerprint(service, cookies)
    lst = data.setdefault(service, [])
    if any(s.get("fp") == fp and s["id"] != replace_id for s in lst):
        return False, "This cookie is already added.", None
    if replace_id is None and len(lst) >= MAX_SLOTS:
        return False, f"Max {MAX_SLOTS} cookies for {SERVICES[service][0]}. Delete one first.", None
    slot_id = replace_id or secrets.token_hex(4)
    os.makedirs(os.path.join(ROOT, service), exist_ok=True)
    tmp = _path(service, slot_id) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, _path(service, slot_id))
    slot = {"id": slot_id, "on": True, "added": int(time.time()), "exp": exp, "fp": fp, "fails": 0, "down": False}
    if replace_id:
        for i, s in enumerate(lst):
            if s["id"] == replace_id:
                lst[i] = slot
    else:
        lst.append(slot)
    return True, "saved", slot


def add(service, content, replace_id=None):
    """Validate and store one cookies.txt. Returns (ok, message, slot number or None)."""
    with _lock:
        data = _all()
        ok, msg, slot = _add(service, content, data, replace_id)
        if ok:
            _save(data)
            n = next(i for i, s in enumerate(data[service], 1) if s["id"] == slot["id"])
            return True, msg, n
        return False, msg, None


def toggle(service, slot_id) -> None:
    with _lock:
        data = _all()
        for s in data.get(service, []):
            if s["id"] == slot_id:
                s["on"] = not s["on"]
                if s["on"]:
                    s.update(fails=0, down=False)
        _save(data)


def delete(service, slot_id) -> None:
    with _lock:
        data = _all()
        data[service] = [s for s in data.get(service, []) if s["id"] != slot_id]
        _save(data)
        try:
            os.remove(_path(service, slot_id))
        except OSError:
            pass


# ---------------- use ----------------
def usable(s) -> bool:
    return s["on"] and not s.get("down") and not (s.get("exp") and s["exp"] < time.time())


def active_paths(service) -> list:
    """Cookie files to try, in order (ON, not down, not expired)."""
    return [_path(service, s["id"]) for s in slots(service) if usable(s) and os.path.exists(_path(service, s["id"]))]


def first_active(service):
    paths = active_paths(service)
    return paths[0] if paths else None


def service_of_path(path):
    """(service, slot id) for a cookie file of this store, else (None, None)."""
    try:
        rel = os.path.relpath(os.path.abspath(path), ROOT).split(os.sep)
        if len(rel) == 2 and rel[0] in SERVICES:
            return rel[0], rel[1][:-4]
    except Exception:
        pass
    return None, None


def record(path, ok: bool, reason: str = "") -> None:
    """Result of a real download with this cookie: 5 failures in a row -> down + DM owners."""
    service, slot_id = service_of_path(path) if path else (None, None)
    if not service:
        return
    alert = None
    with _lock:
        data = _all()
        for n, s in enumerate(data.get(service, []), 1):
            if s["id"] != slot_id:
                continue
            if ok:
                if s.get("down"):
                    alert = f"✅ <b>{SERVICES[service][0]} Cookie-{n} is working again.</b>"
                s.update(fails=0, down=False, last_ok=int(time.time()))
            else:
                s["fails"] = s.get("fails", 0) + 1
                if s["fails"] >= FAIL_ALERT_AT and not s.get("down"):
                    s["down"] = True
                    alert = (f"🚨 <b>{SERVICES[service][0]} Cookie-{n} stopped working</b>\n<blockquote><b>"
                             f"{FAIL_ALERT_AT} downloads in a row failed{(': ' + reason[:120]) if reason else ''}.\n"
                             f"The bot now skips it. Admin Panel → Cookie Manager → add a fresh cookie.</b></blockquote>")
        _save(data)
    if alert:
        alert_owners(alert)


def alert_owners(text: str) -> None:
    try:
        from HELPERS.app_instance import get_app
        from COMMANDS.admin_panel import OWNERS
        from pyrogram import enums
        app = get_app()
        for uid in OWNERS:
            try:
                app.send_message(uid, text, parse_mode=enums.ParseMode.HTML)
            except Exception as exc:
                logger.info(f"[cookies] alert to {uid} failed: {exc}")
    except Exception as exc:
        logger.warning(f"[cookies] alert failed: {exc}")


def exp_text(ts) -> str:
    if not ts:
        return "no expiry date"
    d = datetime.fromtimestamp(ts, IST)
    left = (ts - time.time()) / 86400
    return f"{'expired' if left < 0 else 'till'} {d:%d %b %Y}" + (f" · {int(left)}d left" if left >= 0 else "")


# ---------------- checks ----------------
def test_slot(service, slot_id):
    """(ok, reason). YouTube is tested live; others: login cookie present and not expired."""
    p = _path(service, slot_id)
    try:
        with open(p, "r", encoding="utf-8", errors="ignore") as f:
            cookies = parse(f.read())
    except OSError:
        return False, "file missing"
    if not any(n in cookies for n in SERVICES[service][3]):
        return False, "login cookie missing"
    exp = _login_expiry(service, cookies)
    if exp and exp < time.time():
        return False, "expired"
    if service == "youtube":
        return _youtube_live(p)
    if service in _LOGIN_PAGES:
        return _web_live(service, cookies)
    return True, "login cookie present"


# A logged-in page: a dead cookie gets redirected away
# (Facebook answers 400 to server requests either way, so it keeps the login-cookie check only)
_LOGIN_PAGES = {
    "instagram": "https://www.instagram.com/accounts/edit/",
}


def _web_live(service, cookies):
    import requests
    try:
        r = requests.get(_LOGIN_PAGES[service], cookies={k: v[0] for k, v in cookies.items()}, allow_redirects=False,
                         timeout=20, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"})
    except Exception as exc:
        return True, f"login cookie present (live check failed: {str(exc)[:40]})"
    if r.status_code in (301, 302, 303, 307):  # logged out -> sent to the home / login page
        return False, f"{SERVICES[service][0]} says logged out (export a fresh cookie)"
    if r.status_code == 200:
        return True, "working"
    return True, f"login cookie present (live check: HTTP {r.status_code})"


def _youtube_live(path):
    """Real check: the cookie must give YouTube formats (a copy is used so yt-dlp can't rewrite the file)."""
    import shutil
    import tempfile
    import yt_dlp
    tmp = tempfile.NamedTemporaryFile(suffix=".txt", delete=False).name
    try:
        shutil.copyfile(path, tmp)
        opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
                "socket_timeout": 20, "cookiefile": tmp}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(os.getenv("YOUTUBE_COOKIE_TEST_URL") or "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                                    download=False)
        return (True, "working") if info and info.get("formats") else (False, "no formats")
    except Exception as exc:
        err = str(exc)
        if "not a bot" in err or "Sign in" in err:
            return False, "YouTube asks to sign in (cookie dead, export a fresh one)"
        return False, f"test error: {err[:80]}"
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def check_all(alert=True) -> list:
    """Test every ON cookie; DM owners about changes and expiry (1 day before / on the day)."""
    lines, alerts = [], []
    now = time.time()
    for service in SERVICES:
        for n, s in enumerate(slots(service), 1):
            tag = f"{SERVICES[service][0]} Cookie-{n}"
            if not s["on"]:
                lines.append(f"⚪ {tag} — OFF")
                continue
            ok, why = test_slot(service, s["id"])
            lines.append(f"{'✅' if ok else '❌'} {tag} — {why} · {exp_text(s.get('exp'))}")
            with _lock:
                data = _all()
                cur = next((x for x in data.get(service, []) if x["id"] == s["id"]), None)
                if cur is None:
                    continue
                if not ok and not cur.get("down"):
                    cur["down"] = True
                    alerts.append(f"❌ {tag} — {why}")
                elif ok and cur.get("down") and why != "login cookie present":
                    cur.update(down=False, fails=0)
                    alerts.append(f"✅ {tag} — working again")
                exp = cur.get("exp") or 0
                if exp and 0 < exp - now <= 86400 and not cur.get("warned"):
                    cur["warned"] = True
                    alerts.append(f"⏳ {tag} expires within 24 hours ({exp_text(exp)})")
                _save(data)
    if alert and alerts:
        alert_owners("🍪 <b>Cookie Manager</b>\n<blockquote><b>" + "\n".join(alerts) +
                     "\n\nAdmin Panel → Cookie Manager → add fresh cookies.</b></blockquote>")
    return lines


def _watch():
    time.sleep(180)
    while True:
        try:
            check_all(alert=True)
        except Exception as exc:
            logger.warning(f"[cookies] check failed: {exc}")
        time.sleep(CHECK_EVERY_HOURS * 3600)


threading.Thread(target=_watch, daemon=True, name="cookie-watch").start()
