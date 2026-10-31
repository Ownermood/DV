"""Optional TeraBox public-share adapter.

This adapter uses a third-party extraction endpoint and is disabled by default.
It is intentionally independent of the unlicensed source ZIP. Endpoint availability,
terms and response formats can change; never treat this as a guaranteed service.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path
from urllib.parse import urlparse

import requests

TERABOX_HOSTS = {
    "terabox.com",
    "terabox.app",
    "terabox.fun",
    "terabox.best",
    "terabox.club",
    "terabox.click",
    "terabox.me",
    "terabox.site",
    "terabox.pro",
    "terabox.xyz",
    "teraboxapp.com",
    "teraboxlink.com",
    "teraboxlinke.com",
    "teraboxshare.com",
    "teraboxsharefile.com",
    "teraboxurl.com",
    "teraboxfree.com",
    "teraboxmod.app",
    "terasharelink.com",
    "terasharefile.com",
    "terashareus.com",
    "terafileshare.com",
    "terasharedrive.com",
    "tera1024box.com",
    "1024tera.com",
    "1024tera.co",
    "1024terabox.com",
    "1024-terabox.com",
    "1024box.com",
    "4funbox.com",
    "4funbox.co",
    "4funbox.in",
    "mirrobox.com",
    "nephobox.com",
    "freeterabox.com",
    "momerybox.com",
    "tibibox.com",
    "gibibox.com",
    "pebibox.com",
    "fancybox.in",
    "dubox.com",
    "bestclouddrive.com",
    "playduo.link",
}
ENABLED = os.getenv("TERABOX_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
MAX_BYTES = int(float(os.getenv("TERABOX_MAX_BYTES", str(1900 * 1024 * 1024))))
CONNECT_TIMEOUT = float(os.getenv("TERABOX_CONNECT_TIMEOUT", "10"))
READ_TIMEOUT = float(os.getenv("TERABOX_READ_TIMEOUT", "30"))


def is_terabox_url(url: str) -> bool:
    try:
        parsed = urlparse(url.strip())
        host = (parsed.hostname or "").lower().rstrip(".")
        return parsed.scheme in {"http", "https"} and any(host == d or host.endswith("." + d) for d in TERABOX_HOSTS)
    except (ValueError, AttributeError):
        return False


def _download_thumb(url: str | None, folder: Path) -> str | None:
    if not url or urlparse(url).scheme not in {"http", "https"}:
        return None
    try:
        with requests.get(url, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT), stream=True) as r:
            r.raise_for_status()
            content_type = r.headers.get("Content-Type", "")
            if not content_type.startswith("image/"):
                return None
            path = folder / "thumb.jpg"
            with path.open("wb") as f:
                for chunk in r.iter_content(64 * 1024):
                    if chunk:
                        f.write(chunk)
                        if f.tell() > 5 * 1024 * 1024:
                            f.close()
                            path.unlink(missing_ok=True)
                            return None
            return str(path)
    except requests.RequestException:
        return None


# ---------------- resolvers ----------------
# 1) native: share page (jsToken, logid) -> /share/list. Without a login it gives name/size only;
#    with the admin's TeraBox cookie (cookies/terabox.txt, "ndus") it also gives the dlink.
# 2) token API (like the ntmterabox.me API): TERABOX_API_URL + TERABOX_API_TOKEN in .env,
#    GET ?authkey=TOKEN&url=LINK -> {"ok": true, "files": [{"download_url", "filename", "size"}]}
COOKIE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cookies", "terabox.txt")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")
TOKEN_API_URL = os.getenv("TERABOX_API_URL", "").strip()
# Your own Cloudflare worker from github.com/mkhossainx/Terabox-Downloader-API (deployed with your cookie)
WORKER_URL = os.getenv("TERABOX_WORKER_URL", "").strip().rstrip("/")
TOKEN_API_TOKEN = os.getenv("TERABOX_API_TOKEN", "").strip()
PART_BYTES = int(1.95 * 1024 ** 3)       # Telegram bot upload limit per file
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".webm", ".m4v", ".avi", ".flv", ".ts"}


def _max_bytes() -> int:
    try:
        from CONFIG.config import Config
        return int(float(getattr(Config, "MAX_FILE_SIZE_GB", 4)) * 1024 ** 3)
    except Exception:
        return MAX_BYTES


def _load_cookies(path: str = COOKIE_FILE) -> dict:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            rows = [l.rstrip("\n").split("\t") for l in f if l.strip() and not l.startswith("#")]
    except OSError:
        return {}
    return {r[5]: r[6] for r in rows if len(r) == 7}


def _cookie_files() -> list:
    """Cookie Manager TeraBox cookies (rotation order); the old single file as a fallback."""
    try:
        from COMMANDS import cookie_store
        paths = cookie_store.active_paths("terabox")
    except Exception:
        paths = []
    return paths or ([COOKIE_FILE] if os.path.exists(COOKIE_FILE) else [])


def _between(text: str, start: str, end: str) -> str | None:
    i = text.find(start)
    if i < 0:
        return None
    j = text.find(end, i + len(start))
    return text[i + len(start):j] if j > i else None


class NeedCookie(RuntimeError):
    """TeraBox showed the file but gives a download link only to a logged-in account."""

    def __init__(self, title, size):
        super().__init__("TeraBox needs a login cookie for this download")
        self.title, self.size = title, size


def _native_resolve(url: str, cookies: dict | None = None):
    """(direct_url, thumb, title, size, cookies) via TeraBox's own web API, or raise."""
    cookies = _load_cookies() if cookies is None else cookies
    sess = requests.Session()
    sess.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    sess.cookies.update(cookies)
    page = sess.get(url, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT), allow_redirects=True)
    page.raise_for_status()
    html = page.text
    js_token = _between(html, "fn%28%22", "%22%29") or _between(html, '"jsToken":"', '"')
    logid = _between(html, "dp-logid=", "&") or ""
    from urllib.parse import parse_qs
    final = urlparse(page.url)
    surl = (parse_qs(final.query).get("surl") or [""])[0]
    if not surl:
        m = re.search(r"/s/1?([\w-]+)", final.path) or re.search(r"/s/1?([\w-]+)", urlparse(url).path)
        surl = m.group(1) if m else ""
    if not js_token or not surl:
        raise RuntimeError("TeraBox page changed or the link is invalid")
    base = f"{final.scheme}://{final.hostname}"
    params = {"app_id": "250528", "web": "1", "channel": "dubox", "clienttype": "0", "jsToken": js_token,
              "dplogid": logid, "page": "1", "num": "20", "order": "time", "desc": "1",
              "site_referer": page.url, "shorturl": surl, "root": "1"}
    data = sess.get(f"{base}/share/list", params=params, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)).json()
    _check_share(data)
    files = [f for f in data.get("list") or [] if not int(f.get("isdir") or 0)]
    if not files:
        raise DeadLink("No file in this TeraBox share")
    f = max(files, key=lambda x: int(x.get("size") or 0))
    title, size = f.get("server_filename") or "TeraBox video", int(f.get("size") or 0)
    direct = f.get("dlink")
    if not direct or "ndus" not in cookies:
        raise NeedCookie(title, size)
    thumbs = f.get("thumbs") or {}
    return direct, thumbs.get("url3") or thumbs.get("url2"), title, size, sess.cookies.get_dict()


DEAD_TEXT = ("📦 <b>This TeraBox link doesn't work any more.</b>\n<blockquote><b>The uploader cancelled it, it expired, "
             "or the file was deleted. Ask for a new link.</b></blockquote>")


class DeadLink(RuntimeError):
    """The share itself is gone (cancelled, expired, emptied): nothing else can download it."""


DEAD_ERRNOS = {-21: "cancelled by the uploader or expired", 2130: "empty: the file was deleted"}


def _check_share(data) -> None:
    errno = data.get("errno")
    if errno in (0, None):
        return
    if errno in DEAD_ERRNOS:
        raise DeadLink(f"TeraBox link {DEAD_ERRNOS[errno]} (errno {errno})")
    raise RuntimeError(f"TeraBox refused the share (errno {errno})")


def _token_api_resolve(url: str):
    if not TOKEN_API_URL:
        raise RuntimeError("TERABOX_API_URL not set")
    params = {"url": url}
    if TOKEN_API_TOKEN:
        params["authkey"] = TOKEN_API_TOKEN
    with requests.get(TOKEN_API_URL, params=params, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)) as r:
        r.raise_for_status()
        data = r.json()
    files = [f for f in (data.get("files") or []) if f.get("download_url")] if data.get("ok") else []
    if not files:
        raise RuntimeError("token API returned no file")
    f = files[0]
    return f["download_url"], None, f.get("filename") or "TeraBox video", int(f.get("size") or 0), {}


def _worker_resolve(url: str):
    """POST {"link": url} -> {file_name, download_link, thumbnail, size_bytes, proxy_url}; download via proxy_url."""
    if not WORKER_URL:
        raise RuntimeError("TERABOX_WORKER_URL not set")
    with requests.post(WORKER_URL + "/", json={"link": url}, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)) as r:
        data = r.json()
    if data.get("error"):
        raise RuntimeError(f"worker: {data['error']}")
    direct = data.get("proxy_url") or data.get("download_link")
    if not direct or urlparse(direct).scheme not in {"http", "https"}:
        raise RuntimeError("worker returned no download link")
    return direct, data.get("thumbnail") or None, data.get("file_name") or "TeraBox video", int(data.get("size_bytes") or 0), {}


def _record(path, ok, reason=""):
    if not path:
        return
    try:
        from COMMANDS import cookie_store
        cookie_store.record(path, ok, reason)
    except Exception:
        pass


def _resolve(url: str):
    """First working resolver. On total failure raises; NeedCookie (with name/size) if TeraBox saw the file."""
    errors, need = [], None
    # 1) each TeraBox cookie in turn (Cookie Manager rotation), or no cookie for name/size
    for path in _cookie_files() or [None]:
        try:
            res = _native_resolve(url, _load_cookies(path) if path else {})
            _record(path, True)
            return res
        except NeedCookie as exc:
            need = exc
            _record(path, False, "TeraBox gave no download link")
        except DeadLink:
            raise   # the share is gone: no other resolver can help
        except Exception as exc:
            errors.append(f"native: {exc}")
    for fn in (_worker_resolve, _token_api_resolve):
        try:
            return fn(url)
        except NeedCookie as exc:
            need = exc
        except Exception as exc:
            errors.append(f"{fn.__name__}: {exc}")
    print(f"[terabox] resolvers failed: {'; '.join(errors)}")
    if need:
        raise need
    raise RuntimeError("; ".join(errors))


# ---------------- streaming version (smaller, much faster, like other TeraBox bots) ----------------
def _hls_download(playlist: str, out: Path, limit, progress, cookies=None, referer="https://www.1024tera.com/") -> None:
    """Fetch every segment with 16 connections, join them, remux to mp4 (no re-encode)."""
    import concurrent.futures
    import subprocess
    segs = [l.strip() for l in playlist.splitlines() if l.strip().startswith("http")]
    if not segs:
        raise RuntimeError("empty playlist")
    sess = requests.Session()
    sess.headers.update({"User-Agent": UA, "Referer": referer})  # segments answer 403 "hotlinking" without it
    if cookies:
        sess.cookies.update(cookies)
    adapter = requests.adapters.HTTPAdapter(pool_connections=PARTS, pool_maxsize=PARTS)
    sess.mount("https://", adapter)
    folder = out.parent / "segs"
    folder.mkdir(exist_ok=True)
    lock, done, n_done = threading.Lock(), [0], [0]

    def get(i, u):
        for attempt in range(4):
            try:
                data = sess.get(u, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)).content
                (folder / f"{i:06d}.ts").write_bytes(data)
                with lock:
                    done[0] += len(data)
                    n_done[0] += 1
                    cur, k = done[0], n_done[0]
                if cur > limit:
                    raise RuntimeError(f"too large (limit {limit / 1024 ** 3:.0f} GB)")
                # % from segments done (exact); the size is only a guess from the segments so far
                progress(cur, int(cur * len(segs) / k), k / len(segs))
                return
            except RuntimeError:
                raise
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(2 * (attempt + 1))
    with concurrent.futures.ThreadPoolExecutor(PARTS) as pool:
        for fut in [pool.submit(get, i, u) for i, u in enumerate(segs)]:
            fut.result()
    joined = out.parent / "joined.ts"
    with joined.open("wb") as w:
        for i in range(len(segs)):
            part = folder / f"{i:06d}.ts"
            w.write(part.read_bytes())
            part.unlink()
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(joined), "-c", "copy", "-bsf:a", "aac_adtstoasc",
                    "-movflags", "+faststart", str(out)], check=True, timeout=1800)
    joined.unlink()


# ---------------- send ----------------
def _duration(path: str) -> float:
    import subprocess
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                              "default=noprint_wrappers=1:nokey=1", path], capture_output=True, text=True, timeout=60)
        return float(out.stdout.strip() or 0)
    except Exception:
        return 0.0


def _caption(title: str, url: str, part: str = "") -> str:
    from html import escape
    from HELPERS.caption import build_quoted_caption
    bot = os.getenv("BOT_NAME", "Downloader_Villanbot")
    cap = build_quoted_caption(f"<b>{escape(title[:300])}</b>", "", "", "", f'<a href="{escape(url)}">🔗 TeraBox</a> @{bot}')
    return f"<b>🎞 {part}</b>\n{cap}" if part else cap  # parts: bold on top, outside the quotes


PARTS = 16                    # parallel connections (TeraBox throttles each connection)
MIN_PART = 4 * 1024 * 1024


def _fast_download(url, filepath: Path, cookies, size, limit, progress) -> None:
    """Parallel HTTP range download (16 connections); single stream if the server has no ranges."""
    import concurrent.futures
    headers = {"User-Agent": UA, "Referer": "https://terabox.com/"}
    sess = requests.Session()
    sess.headers.update(headers)
    if cookies:
        sess.cookies.update(cookies)
    adapter = requests.adapters.HTTPAdapter(pool_connections=PARTS, pool_maxsize=PARTS)
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    probe = sess.get(url, headers={"Range": "bytes=0-0"}, stream=True, allow_redirects=True,
                     timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
    final, code = probe.url, probe.status_code
    rng = probe.headers.get("Content-Range", "")
    probe.close()
    length = int(rng.rsplit("/", 1)[-1]) if code == 206 and "/" in rng and rng.rsplit("/", 1)[-1].isdigit() else 0
    if length and length > limit:
        raise RuntimeError(f"too large: {length / 1024 ** 3:.2f} GB (limit {limit / 1024 ** 3:.0f} GB)")
    if size and length and length < size * 0.9:  # TeraBox answers some servers with a small placeholder file
        raise RuntimeError(f"TeraBox sent {length / 1024 ** 2:.1f} MB instead of {size / 1024 ** 2:.0f} MB "
                           f"(original blocked for this account)")
    lock, done = threading.Lock(), [0]

    def add(n, total_len):
        with lock:
            done[0] += n
            cur = done[0]
        if cur > limit:
            raise RuntimeError(f"too large (limit {limit / 1024 ** 3:.0f} GB)")
        progress(cur, total_len)

    print(f"[terabox] download: HTTP {code}, size {length or size or 0}, "
          f"{'parallel' if length and length >= 2 * MIN_PART else 'single stream'}", flush=True)
    if not length or length < 2 * MIN_PART:  # no range support / small file: one stream
        with sess.get(final, stream=True, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)) as r:
            r.raise_for_status()
            total_len = int(r.headers.get("Content-Length") or size or 0)
            if total_len and total_len > limit:
                raise RuntimeError(f"too large: {total_len / 1024 ** 3:.2f} GB (limit {limit / 1024 ** 3:.0f} GB)")
            with filepath.open("wb") as f:
                for chunk in r.iter_content(256 * 1024):
                    if chunk:
                        f.write(chunk)
                        add(len(chunk), total_len)
        return
    n = max(1, min(PARTS, length // MIN_PART))
    step = length // n
    ranges = [(i * step, length - 1 if i == n - 1 else (i + 1) * step - 1) for i in range(n)]
    with filepath.open("wb") as f:
        f.truncate(length)

    def part(a, b):
        for attempt in range(4):
            pos = a
            try:
                with sess.get(final, headers={"Range": f"bytes={a}-{b}"}, stream=True,
                              timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)) as r:
                    if r.status_code != 206:
                        raise RuntimeError(f"range not served (HTTP {r.status_code})")
                    with filepath.open("r+b") as f:
                        f.seek(a)
                        for chunk in r.iter_content(256 * 1024):
                            if chunk:
                                f.write(chunk)
                                pos += len(chunk)
                                add(len(chunk), length)
                if pos == b + 1:
                    return
                raise RuntimeError("part cut short")
            except Exception as exc:
                if type(exc).__name__ == "Cancelled":  # ❌ Cancel: no retries
                    raise
                with lock:
                    done[0] -= pos - a  # this part restarts from its beginning
                if attempt == 3:
                    raise
                time.sleep(2 * (attempt + 1))
    with concurrent.futures.ThreadPoolExecutor(n) as pool:
        for fut in [pool.submit(part, a, b) for a, b in ranges]:
            fut.result()


def _preview(app, message, title, size, thumb_path, quality=""):
    """One card for the whole job: thumbnail, name, size; progress is added below. Returns (msg, base text)."""
    from html import escape
    from pyrogram import enums
    try:
        from COMMANDS.ui_kit import pem
        icon, sz = pem("box"), pem("folder")
    except Exception:
        icon, sz = "📦", "📁"
    size_txt = (f"{size / 1024 ** 3:.2f} GB" if size >= 1024 ** 3 else f"{size / 1024 ** 2:.1f} MB") if size else ""
    line = f"Size: {size_txt}" if size_txt else f"Quality: {quality} (fast stream copy)" if quality else ""
    base = f"{icon} <b>{escape(title[:180])}</b>" + (f"\n<b>{sz} {line}</b>" if line else "")
    try:
        if thumb_path:
            msg = app.send_photo(message.chat.id, thumb_path, caption=base, parse_mode=enums.ParseMode.HTML,
                                 reply_to_message_id=message.id, **_topic_kw(message))
        else:
            msg = app.send_message(message.chat.id, base, parse_mode=enums.ParseMode.HTML, reply_to_message_id=message.id,
                                   **_topic_kw(message))
        return msg, base
    except Exception as exc:
        print(f"[terabox] preview failed: {exc}")
        return None, base


def _topic_kw(message) -> dict:
    """Forum groups: stay in the topic the link came from."""
    tid = getattr(message, "message_thread_id", None)
    return {"message_thread_id": tid} if tid and getattr(message, "is_topic_message", False) else {}


def _show(app, msg, text, markup=None) -> None:
    """Edit the job card (photo caption or text)."""
    from pyrogram import enums
    try:
        if getattr(msg, "photo", None):
            app.edit_message_caption(msg.chat.id, msg.id, text[:1024], parse_mode=enums.ParseMode.HTML,
                                     reply_markup=markup)
        else:
            app.edit_message_text(msg.chat.id, msg.id, text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
    except Exception:
        pass


# ---------------- cache: the same share link is sent again instantly ----------------
_CACHE_MAX = 3000


def _share_key(url):
    """Share code (+ '#720' etc. for a streaming copy), same for every mirror domain."""
    m = re.search(r"(?:/s/|surl=)1?([\w-]+)", url or "")
    q = (url or "").partition("#")[2]
    return (m.group(1) + (f"#{q}" if q else "")) if m else None


def _cache_get(url):
    key = _share_key(url)
    if not key:
        return None
    try:
        from COMMANDS.ui_kit import state_get
        return (state_get("terabox_cache", {}) or {}).get(key)
    except Exception:
        return None


def _cache_put(url, sent, title=""):
    key = _share_key(url)
    items = [(m.video.file_id, "v") if m.video else (m.document.file_id, "d") for m in sent if m and (m.video or m.document)]
    if not key or not items:
        return
    try:
        from COMMANDS.ui_kit import state_get, state_set
        cache = state_get("terabox_cache", {}) or {}
        cache[key] = {"t": title[:200], "f": items}
        while len(cache) > _CACHE_MAX:
            cache.pop(next(iter(cache)))
        state_set("terabox_cache", cache)
    except Exception as exc:
        print(f"[terabox] cache save failed: {exc}")


def _send_cached(app, message, url, entry) -> bool:
    chat, rid = message.chat.id, message.id
    items, title = (entry.get("f") or [], entry.get("t") or "TeraBox") if isinstance(entry, dict) else (entry, "TeraBox")
    try:
        for n, (fid, kind) in enumerate(items, 1):
            cap = _caption(title, url, f"Part {n}/{len(items)}" if len(items) > 1 else "")
            if kind == "v":
                app.send_video(chat, fid, caption=cap, reply_to_message_id=rid, supports_streaming=True)
            else:
                app.send_document(chat, fid, caption=cap, reply_to_message_id=rid)
        return True
    except Exception as exc:
        print(f"[terabox] cached send failed, downloading again: {exc}")
        return False


def _split_by_size(src: Path, limit: int) -> list:
    """Cut a too-big video into parts of at most `limit` bytes, each starting where the last one ended.
    Needs no total duration: TeraBox .ts recordings often report a wrong one (a 2h38m video read as ~15 min,
    so time-based parts covered only the start)."""
    import subprocess
    paths, start = [], 0.0
    for n in range(1, 30):
        out = src.parent / f"part{n}.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-fflags", "+genpts", "-i", str(src), "-ss", f"{start:.3f}",
                        "-map", "0:v:0", "-map", "0:a?", "-c", "copy", "-fs", str(limit),
                        "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", str(out)],
                       check=False, timeout=3600)
        size = out.stat().st_size if out.exists() else 0
        dur = _duration(str(out)) if size else 0
        if size < 1024 or dur <= 1:
            out.unlink(missing_ok=True)
            break
        paths.append(str(out))
        if size < limit * 0.9:   # this part reached the end of the video
            break
        start += dur
    return paths


def _send_file(app, message, filepath: Path, title: str, url: str, thumb_path, on_progress=None, cover=None):
    """Send the file; videos over the Telegram limit go out in parts. on_progress(done, total) during upload."""
    chat, rid = message.chat.id, message.id
    size = filepath.stat().st_size
    is_video = filepath.suffix.lower() in VIDEO_EXT
    kw = {"progress": on_progress} if on_progress else {}
    if size <= PART_BYTES:
        if is_video:
            return [app.send_video(chat, str(filepath), caption=_caption(title, url), thumb=thumb_path, cover=cover,
                                   reply_to_message_id=rid, supports_streaming=True, **kw)]
        return [app.send_document(chat, str(filepath), caption=_caption(title, url), thumb=thumb_path,
                                  reply_to_message_id=rid, **kw)]
    if not is_video:
        raise RuntimeError(f"too large: {size / 1024 ** 3:.2f} GB file (only videos can be sent in parts)")
    paths = _split_by_size(filepath, int(PART_BYTES * 0.97))
    if not paths:
        raise RuntimeError("could not split the video")
    return [app.send_video(chat, p, caption=_caption(title, url, f"Part {n}/{len(paths)}"), thumb=thumb_path, cover=cover,
                           reply_to_message_id=rid, supports_streaming=True, **kw) for n, p in enumerate(paths, 1)]


# ---------------- quality menu ----------------
QUALITIES = ("1080", "720", "480")
GROUP_MENU_TTL = 30           # groups: an unused quality menu is deleted after 30 s
_JOBS = {}                 # token -> (message, url, meta, card message, ts)
_JOBS_TTL = 6 * 3600
_jobs_lock = threading.Lock()


def _meta(url, cookies):
    """Share details needed for the menu and the streaming copies."""
    sess = requests.Session()
    sess.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    sess.cookies.update(cookies)
    page = sess.get(url, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT), allow_redirects=True)
    page.raise_for_status()
    js_token = _between(page.text, "fn%28%22", "%22%29") or _between(page.text, '"jsToken":"', '"')
    logid = _between(page.text, "dp-logid=", "&") or ""
    from urllib.parse import parse_qs
    final = urlparse(page.url)
    m = re.search(r"/s/1?([\w-]+)", urlparse(url).path) or re.search(r"/s/1?([\w-]+)", final.path)
    surl = (parse_qs(final.query).get("surl") or [""])[0] or (m.group(1) if m else "")
    if not js_token or not surl:
        raise RuntimeError("TeraBox page changed or the link is invalid")
    base = f"{final.scheme}://{final.hostname}"
    common = {"app_id": "250528", "web": "1", "channel": "dubox", "clienttype": "0", "jsToken": js_token}
    data = sess.get(f"{base}/share/list", params={**common, "dplogid": logid, "page": "1", "num": "20", "order": "time",
                                                  "desc": "1", "site_referer": page.url, "shorturl": surl, "root": "1"},
                    timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)).json()
    _check_share(data)
    files = [f for f in data.get("list") or [] if not int(f.get("isdir") or 0)]
    if not files:
        raise DeadLink("No file in this TeraBox share")
    f = max(files, key=lambda x: int(x.get("size") or 0))
    info = sess.get(f"{base}/api/shorturlinfo", params={**common, "shorturl": "1" + surl, "root": "1"},
                    timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)).json()
    thumbs = f.get("thumbs") or {}
    return {"sess": sess, "base": base, "common": common, "fid": f.get("fs_id"), "video": str(f.get("category")) == "1",
            "surl": surl,
            "uk": info.get("uk") or data.get("uk"), "shareid": info.get("shareid") or data.get("share_id"),
            "sign": info.get("sign"), "timestamp": info.get("timestamp"), "duration": float(f.get("duration") or 0),
            "title": f.get("server_filename") or "TeraBox video", "size": int(f.get("size") or 0),
            # 1920x800 counts as 1080p
            "height": max(int(f.get("height") or 0), int(f.get("width") or 0) * 9 // 16),
            "thumb": thumbs.get("url3") or thumbs.get("url2"),
            "logged_in": "ndus" in cookies}


def _meta_try(url):
    """Meta with the first TeraBox cookie that works (logged in = full streaming copies), else without one."""
    last = None
    for path in _cookie_files() + [None]:
        try:
            return _meta(url, _load_cookies(path) if path else {})
        except DeadLink:
            raise   # same answer with every cookie
        except Exception as exc:
            last = exc
    raise last or RuntimeError("TeraBox link failed")


_own_lock = threading.Lock()
OWN_DIR = "/dvbot"  # shared files are copied here: TeraBox gives 720p / 1080p only for files in your own account


def _own_copy(meta):
    """Copy the shared file into the cookie's own account (once per menu); returns its path there."""
    with _own_lock:  # 720p and 1080p are checked in parallel: copy only once
        if not meta.get("own_path"):
            meta["own_path"] = _own_copy_now(meta)
    return meta["own_path"]


def _own_copy_now(meta):
    sess, base, common = meta["sess"], meta["base"], meta["common"]
    page = sess.get(f"{base}/main", headers={"Referer": f"{base}/"}, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)).text
    bds = re.search(r'["\']bdstoken["\']?\s*[:=]\s*["\']([^"\']+)', page)
    if not bds:
        raise RuntimeError("TeraBox cookie expired (no bdstoken)")
    meta["bdstoken"] = bds = bds.group(1)
    info = sess.get(f"{base}/api/shorturlinfo", params={**common, "shorturl": "1" + meta["surl"], "root": "1"},
                    timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)).json()
    randsk = urllib.parse.unquote(info.get("randsk", ""))
    if randsk:
        sess.cookies.set("TSID", randsk)
    # the folder (fine if it already exists)
    sess.post(f"{base}/api/create", params={**common, "bdstoken": bds},
              data={"path": OWN_DIR, "isdir": "1", "block_list": "[]"}, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
    tr = sess.post(f"{base}/share/transfer", params={**common, "shareid": meta["shareid"], "from": meta["uk"],
                                                     "sekey": randsk, "ondup": "newcopy", "async": "2", "bdstoken": bds},
                   data={"fsidlist": json.dumps([int(meta["fid"])]), "path": OWN_DIR},
                   headers={"Referer": f"{base}/", "Origin": base}, timeout=(CONNECT_TIMEOUT, 30)).json()
    if tr.get("errno") not in (0, 4, 12):
        raise RuntimeError(f"copy to own account failed (errno {tr.get('errno')})")
    path = None
    for _ in range(5):  # the copy shows up in the list after a moment
        time.sleep(1.5)
        lst = sess.get(f"{base}/api/list", params={**common, "dir": OWN_DIR, "num": "100", "order": "time", "desc": "1"},
                       timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)).json()
        mine = next((f for f in lst.get("list") or [] if int(f.get("size") or 0) == meta["size"]
                     and Path(f.get("server_filename", "")).stem.startswith(Path(meta["title"]).stem[:40])), None)
        if mine:
            path = mine["path"]
            break
    if not path:
        raise RuntimeError("copied file not found in own account")
    return path


def _own_delete(meta) -> None:
    """Remove the copy again so the account does not fill up."""
    path = meta.get("own_path") if meta else None
    if not path:
        return
    try:
        meta["sess"].post(f"{meta['base']}/api/filemanager",
                          params={**meta["common"], "opera": "delete", "async": "2", "bdstoken": meta.get("bdstoken", "")},
                          data={"filelist": json.dumps([path])}, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
        meta["own_path"] = None
    except Exception as exc:
        print(f"[terabox] own copy delete failed: {exc}", flush=True)


def _own_playlist(meta, q):
    path = _own_copy(meta)
    r = meta["sess"].get(f"{meta['base']}/api/streaming", params={
        **meta["common"], "path": path, "type": f"M3U8_AUTO_{q}", "bdstoken": meta.get("bdstoken", ""),
        "isplayer": "1", "check_blue": "1", "clienttype": "1", "resolution": f"{q}p"},
        timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
    if not r.text.startswith("#EXTM3U"):
        raise RuntimeError(f"{q}p is not available for this video")
    return r.text


def _playlist(meta, q):
    """Streaming copy: from the share first; 720p / 1080p via a copy in the own account (needs Premium)."""
    try:
        return _share_playlist(meta, q)
    except Exception:
        if not meta.get("logged_in") or q == "480":
            raise
    return _own_playlist(meta, q)


def _share_playlist(meta, q):
    r = meta["sess"].get(f"{meta['base']}/share/streaming", params={
        **meta["common"], "uk": meta["uk"], "shareid": meta["shareid"], "type": f"M3U8_AUTO_{q}", "fid": meta["fid"],
        "sign": meta["sign"], "timestamp": meta["timestamp"], "esl": "1", "isplayer": "1", "ehps": "1"},
        timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
    text = r.text
    if not text.startswith("#EXTM3U"):
        raise RuntimeError(f"{q}p is not available for this video")
    length = sum(float(x) for x in re.findall(r"#EXTINF:([\d.]+)", text))
    if meta["duration"] and length < meta["duration"] * 0.9:
        raise RuntimeError("only a 30 s preview (TeraBox cookie missing or logged out)")
    return text


def _menu_kb(token, meta):
    from pyrogram.types import InlineKeyboardMarkup
    from COMMANDS.ui_kit import btn
    from pyrogram.enums import ButtonStyle
    plain = ButtonStyle.DEFAULT
    rows = []
    if meta["video"]:
        top = meta["height"] or 1080
        qs = [q for q in QUALITIES if int(q) <= max(top, 480)] or ["480"]
        if meta.get("available") is not None:  # only the copies TeraBox really gives this account
            qs = [q for q in qs if q in meta["available"]]
        rows.append([btn("bolt" if q == "480" else "play", f"{q}p" + (" ⚡" if q == "480" else ""),
                         cb=f"tb|{token}|{q}", style=plain) for q in qs])
    size = meta["size"]
    size_txt = (f"{size / 1024 ** 3:.2f} GB" if size >= 1024 ** 3 else f"{size / 1024 ** 2:.0f} MB") if size else ""
    rows.append([btn("folder", "Original file" + (f" · {size_txt}" if size_txt else ""), cb=f"tb|{token}|o", style=plain)])
    rows.append([btn("trash", "Close", cb=f"tb|{token}|x", style=plain)])
    return InlineKeyboardMarkup(rows)


def _default_quality(message, meta):
    """The person's default TeraBox quality (Settings) as a menu choice, or None (show the menu).
    Best = the original file; a height falls to the next lower stream copy this account gets."""
    try:
        from COMMANDS.user_prefs import quality, person_of
        pref = quality(person_of(message), "terabox")
    except Exception:
        return None
    if not pref or pref == "a":
        return None
    if pref == "b" or not meta.get("video"):
        return "o"
    have = [int(q) for q in QUALITIES if meta.get("available") is None or q in meta["available"]]
    lower = [h for h in have if h <= int(pref)]
    return str(max(lower)) if lower else None


def handle_terabox(app, message) -> bool:
    """TeraBox link: preview card with qualities (streaming copies are fast, the original is full quality)."""
    import secrets
    from HELPERS.progress_ui import progress_card
    from pyrogram import enums
    text = (getattr(message, "text", None) or "").strip()
    match = re.search(r"https?://[^\s<>]+", text)
    url = match.group(0).rstrip(".,);]}") if match else ""
    if not is_terabox_url(url):
        return False
    if not ENABLED:
        app.send_message(message.chat.id, "TeraBox support is disabled.", reply_to_message_id=message.id)
        return True
    status = app.send_message(message.chat.id, progress_card("search", 0), parse_mode=enums.ParseMode.HTML,
                              reply_to_message_id=message.id, **_topic_kw(message))
    try:
        meta = _meta_try(url)
    except Exception as exc:
        print(f"[terabox] {url}: {exc}")
        meta = None
        if isinstance(exc, DeadLink):
            _show(app, status, DEAD_TEXT)
            return True
    if meta is None:  # old resolvers (worker / token API) still get a chance
        _run_job(app, message, url, None, "o", status)
        return True
    if meta["video"]:  # ask TeraBox which streaming copies it gives (1080p / 720p often need Premium)
        import concurrent.futures

        def ok(q):
            try:
                _playlist(meta, q)
                return q
            except Exception:
                return None
        with concurrent.futures.ThreadPoolExecutor(len(QUALITIES)) as pool:
            meta["available"] = {q for q in pool.map(ok, QUALITIES) if q}
    token = secrets.token_hex(4)
    with tempfile.TemporaryDirectory(prefix="tera-thumb-") as d:
        thumb = _download_thumb(meta["thumb"], Path(d))
        card, base = _preview(app, message, meta["title"], meta["size"], thumb)
        if card is None:
            card = status
        else:
            try:
                app.delete_messages(message.chat.id, status.id)
            except Exception:
                pass
    auto = _default_quality(message, meta)
    if auto:  # ⚙️ the person's default quality: no menu, start right away
        threading.Thread(target=_run_job, args=(app, message, url, meta, auto, card), daemon=True).start()
        return True
    hint = ("⚡ Stream copies are the fastest · 📁 Original = the real full-quality file"
            if meta["video"] else "")
    _show(app, card, base + (f"\n<blockquote><b>{hint}</b></blockquote>" if hint else "")
          + "\n<b>Choose quality:</b>", _menu_kb(token, meta))
    now = time.time()
    with _jobs_lock:
        for k in [k for k, v in _JOBS.items() if now - v[4] > _JOBS_TTL]:
            _JOBS.pop(k, None)
        _JOBS[token] = (message, url, meta, card, now)
    if message.chat.id < 0:  # group: an unused menu goes after 30 s, with the /dl message
        def expire():
            with _jobs_lock:
                if _JOBS.pop(token, None) is None:
                    return   # a quality was picked
            _own_delete(meta)
            try:
                app.delete_messages(message.chat.id, [card.id, message.id])
            except Exception:
                pass
        timer = threading.Timer(GROUP_MENU_TTL, expire)
        timer.daemon = True
        timer.start()
    return True


def on_choice(app, cq) -> None:
    """Button under the TeraBox card: tb|token|1080/720/480/o/x."""
    _, token, q = (cq.data.split("|") + ["", ""])[:3]
    with _jobs_lock:
        job = _JOBS.get(token)
    sender = getattr(getattr(job[0], "from_user", None), "id", None) or job[0].chat.id if job else None
    if not job or sender != cq.from_user.id:
        cq.answer("This menu expired. Send the link again.", show_alert=True)
        return
    message, url, meta, card, _ = job
    if q == "x":
        with _jobs_lock:
            _JOBS.pop(token, None)
        cq.answer()
        threading.Thread(target=_own_delete, args=(meta,), daemon=True).start()
        try:
            card.delete()
        except Exception:
            pass
        return
    with _jobs_lock:
        _JOBS.pop(token, None)  # one download per card
    cq.answer("Started")
    threading.Thread(target=_run_job, args=(app, message, url, meta, q, card), daemon=True).start()


def _run_job(app, message, url, meta, q, card) -> None:
    from HELPERS import cancel, group_mirror
    twin = getattr(message, "_dm_twin", None)   # group request: the file goes to the member's DM (+ group copy)
    if twin:
        group_mirror.start(twin.chat.id, message)
        message = twin
    cancel.clear(message.chat.id)  # a new download: an old Cancel tap must not stop it
    cancel.set_owner(message.chat.id, getattr(getattr(message, "from_user", None), "id", None) or message.chat.id)
    try:
        _run_job_inner(app, message, url, meta, q, card)
    finally:
        _own_delete(meta)  # the copy in the own account is not needed any more (a retry copies again)
        if twin:
            group_mirror.stop(app, twin.chat.id)


def _run_job_inner(app, message, url, meta, q, card) -> None:
    """Download the chosen quality (streaming copy or original file) and send it; the card shows the progress."""
    from HELPERS import cancel
    uid = message.chat.id
    from HELPERS.progress_ui import progress_card, edit_later
    from html import escape
    key = url + ("" if q == "o" else f"#{q}")
    try:  # ⚙️ the person's own thumbnail (Settings): such copies stay out of the shared cache
        from COMMANDS.user_prefs import media_for, person_of
        own_thumb, own_cover = media_for(person_of(message), "terabox")
    except Exception:
        own_thumb, own_cover = None, None
    cached = None if own_thumb else _cache_get(key)
    if cached and _send_cached(app, message, url, cached):
        try:
            card.delete()
        except Exception:
            pass
        return
    base = ""
    if meta:
        size = meta["size"]
        line = (f"Quality: {q}p (fast)" if q != "o" else
                f"Original · {size / 1024 ** 3:.2f} GB" if size >= 1024 ** 3 else f"Original · {size / 1024 ** 2:.0f} MB")
        base = f"📦 <b>{escape(meta['title'][:180])}</b>\n<b>{line}</b>"
    limit = _max_bytes()
    try:
        _show(app, card, (base + "\n\n" if base else "") + progress_card("search", 0))
        playlist = None
        if q != "o":
            try:
                playlist = _playlist(meta, q)
            except Exception:  # sign / timestamp expire: fresh share details once
                meta = _meta_try(url)
                playlist = _playlist(meta, q)
            title, thumb_url, size, direct_url, dl_cookies = meta["title"], meta["thumb"], 0, None, None
        else:
            t0 = time.monotonic()
            direct_url, thumb_url, title, size, dl_cookies = _resolve(url)
            print(f"[terabox] original link resolved in {time.monotonic() - t0:.1f}s", flush=True)
            if size and size > limit:
                raise RuntimeError(f"too large: {size / 1024 ** 3:.2f} GB (limit {limit / 1024 ** 3:.0f} GB)")
            if not base:
                base = f"📦 <b>{escape(title[:180])}</b>"
        with tempfile.TemporaryDirectory(prefix="all-downloader-tera-") as temp_dir:
            folder = Path(temp_dir)
            name = (Path(title).stem + f".{q}p.mp4") if playlist else (
                title if "." in title else (Path(urlparse(direct_url).path).name or "terabox-video.mp4"))
            filename = re.sub(r"[^\w .()\-]+", "_", name)[:120]
            if not filename or filename in {".", ".."}:
                filename = "terabox-video.mp4"
            filepath = folder / filename
            thumb_path = own_thumb or _download_thumb(thumb_url, folder)
            state = {"last": 0.0, "start": time.monotonic()}

            def progress(total, length, frac=None):
                cancel.check(uid)  # ❌ Cancel tapped: the download threads stop
                now = time.monotonic()
                if now - state["last"] < 4:  # every 4 s: MB done / total and speed
                    return
                state["last"] = now
                speed = total / max(now - state["start"], 0.1) / 1024 ** 2
                if frac is not None:  # stream copy: real size unknown until the end
                    pct = frac * 100
                    of = f" / ~{length / 1024 ** 2:.0f} MB" if frac >= 0.2 and length else " MB"
                else:
                    length = length or size or 0
                    pct = total * 100 / length if length else 0
                    of = f" / {length / 1024 ** 2:.0f} MB" if length else " MB"
                text = (base + "\n\n" if base else "") + progress_card(
                    "download", pct, detail=f"{total / 1024 ** 2:.0f}{of} · {speed:.1f} MB/s")
                edit_later(lambda: cancel.is_set(uid) or _show(app, card, text, cancel.markup(uid)), card.chat.id, card.id)
            if playlist:
                _hls_download(playlist, filepath, limit, progress, meta["sess"].cookies.get_dict(), meta["base"] + "/")
            else:
                _fast_download(direct_url, filepath, dl_cookies, size, limit, progress)
            _show(app, card, (base + "\n\n" if base else "") + progress_card("upload", 0))
            up = {"last": 0.0, "start": time.monotonic()}

            def upload_progress(done, total):
                cancel.stop_upload(uid)
                now = time.monotonic()
                if now - up["last"] < 4 and done < total:  # every 4 s: MB sent / total and speed
                    return
                up["last"] = now
                speed = done / max(now - up["start"], 0.1) / 1024 ** 2
                text = (base + "\n\n" if base else "") + progress_card(
                    "upload", done * 100 / total if total else 0,
                    detail=f"{done / 1024 ** 2:.0f} / {total / 1024 ** 2:.0f} MB · {speed:.1f} MB/s")
                edit_later(lambda: cancel.is_set(uid) or _show(app, card, text, cancel.markup(uid)), card.chat.id, card.id)
            sent = _send_file(app, message, filepath, title, url, thumb_path, upload_progress, cover=own_cover) or []
            if cancel.is_set(uid):  # upload stopped by ❌ Cancel: keep the "cancelled" card
                return
            if not own_thumb:
                _cache_put(key, sent, title)
        app.delete_messages(card.chat.id, card.id)  # the file itself is the success signal (card may be in a group)
    except Exception as exc:
        if cancel.is_set(uid):  # the Cancel button already replaced the card
            return
        print(f"[terabox] {url} ({q}): {exc}")
        if isinstance(exc, DeadLink):   # the user's link, nothing for the owners to fix
            _show(app, card, DEAD_TEXT)
            return
        try:
            from HELPERS.alerts import download_failed
            download_failed(uid, url, f"TeraBox {q}: {exc}")
        except Exception:
            pass
        if isinstance(exc, NeedCookie):
            body = (f"File: {exc.title[:80]} ({exc.size / 1024 ** 3:.2f} GB)\n"
                    "TeraBox gives download links only to a logged-in account. "
                    "Admin: add TeraBox cookies in Admin Panel → Cookie Manager.")
        elif "instead of" in str(exc):
            body = "TeraBox does not give the original file to this account. Try ⚡ 480p, it is fast."
        elif "too large" in str(exc) or "not available" in str(exc) or "preview" in str(exc):
            body = str(exc)[:200]
        elif q == "o" and meta:  # the share opened fine, so the original file itself stalled
            body = ("TeraBox is limiting the original file's speed on this account (free accounts get a few KB/s). "
                    "Try ⚡ 480p, it is fast.")
        else:
            body = "The link may be private, deleted or need a password."
        retry = None
        if meta:  # another quality may still work
            import secrets
            token = secrets.token_hex(4)
            with _jobs_lock:
                _JOBS[token] = (message, url, meta, card, time.time())
            retry = _menu_kb(token, meta)
        _show(app, card, (base + "\n\n" if base else "")
              + f"📦 <b>TeraBox download failed.</b>\n<blockquote><b>{escape(body)}</b></blockquote>", retry)
