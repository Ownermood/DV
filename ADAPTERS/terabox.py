"""Optional TeraBox public-share adapter.

This adapter uses a third-party extraction endpoint and is disabled by default.
It is intentionally independent of the unlicensed source ZIP. Endpoint availability,
terms and response formats can change; never treat this as a guaranteed service.
"""
from __future__ import annotations

import os
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

TERABOX_HOSTS = {
    "terabox.com", "www.terabox.com", "1024terabox.com", "www.1024terabox.com",
    "1024tera.com", "www.1024tera.com", "terabox.app", "www.terabox.app",
    "teraboxapp.com", "www.teraboxapp.com", "freeterabox.com", "www.freeterabox.com",
    "mirrobox.com", "www.mirrobox.com", "nephobox.com", "www.nephobox.com",
    "4funbox.com", "www.4funbox.com", "momerybox.com", "www.momerybox.com",
    "tibibox.com", "www.tibibox.com",
}
API_ENDPOINT = os.getenv("TERABOX_API_ENDPOINT", "https://ytshorts.savetube.me/api/v1/terabox-downloader")
ENABLED = os.getenv("TERABOX_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
MAX_BYTES = int(float(os.getenv("TERABOX_MAX_BYTES", str(1900 * 1024 * 1024))))
CONNECT_TIMEOUT = float(os.getenv("TERABOX_CONNECT_TIMEOUT", "10"))
READ_TIMEOUT = float(os.getenv("TERABOX_READ_TIMEOUT", "30"))


def is_terabox_url(url: str) -> bool:
    try:
        parsed = urlparse(url.strip())
        return parsed.scheme in {"http", "https"} and (parsed.hostname or "").lower().rstrip(".") in TERABOX_HOSTS
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


def _extract_direct_link(data: dict) -> tuple[str | None, str | None, str | None]:
    items = data.get("response") or data.get("data") or []
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list) or not items:
        return None, None, None
    first = items[0] if isinstance(items[0], dict) else {}
    resolutions = first.get("resolutions") or {}
    if isinstance(resolutions, dict):
        candidate = resolutions.get("HD Video") or resolutions.get("Fast Download") or next(iter(resolutions.values()), None)
    else:
        candidate = None
    direct = first.get("download_url") or first.get("direct_link") or first.get("url") or candidate
    thumb = first.get("thumbnail") or first.get("thumb") or first.get("image")
    title = first.get("file_name") or first.get("name") or first.get("title") or "TeraBox video"
    if isinstance(direct, dict):
        direct = direct.get("url") or direct.get("link")
    return (direct if isinstance(direct, str) else None,
            thumb if isinstance(thumb, str) else None,
            title if isinstance(title, str) else "TeraBox video")


def handle_terabox(app, message) -> bool:
    """Handle a TeraBox URL; return True if this adapter owns the message."""
    text = (getattr(message, "text", None) or "").strip()
    match = re.search(r"https?://[^\s<>]+", text)
    url = match.group(0).rstrip(".,);]}") if match else ""
    if not is_terabox_url(url):
        return False
    if not ENABLED:
        app.send_message(message.chat.id, "TeraBox support is disabled. Set TERABOX_ENABLED=true after reviewing the optional adapter configuration.", reply_to_message_id=message.id)
        return True

    status = app.send_message(message.chat.id, "TeraBox link detected. Resolving public share…", reply_to_message_id=message.id)
    try:
        # Re-check the exact host before sending it to a third-party endpoint.
        if not is_terabox_url(url):
            raise ValueError("Unsupported TeraBox hostname")
        with requests.post(API_ENDPOINT, json={"url": url}, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)) as response:
            response.raise_for_status()
            payload = response.json()
        direct_url, thumb_url, title = _extract_direct_link(payload)
        if not direct_url or urlparse(direct_url).scheme not in {"http", "https"}:
            raise RuntimeError("The extraction service returned no usable direct link")

        with tempfile.TemporaryDirectory(prefix="all-downloader-tera-") as temp_dir:
            folder = Path(temp_dir)
            filename = re.sub(r"[^\w .()\-]+", "_", Path(urlparse(direct_url).path).name or "terabox-video.mp4")[:120]
            if not filename or filename in {".", ".."}:
                filename = "terabox-video.mp4"
            filepath = folder / filename
            thumb_path = _download_thumb(thumb_url, folder)
            total = 0
            last_update = 0.0
            with requests.get(direct_url, stream=True, allow_redirects=True, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)) as response:
                response.raise_for_status()
                length = response.headers.get("Content-Length")
                if length and int(length) > MAX_BYTES:
                    raise RuntimeError("The file exceeds TERABOX_MAX_BYTES")
                with filepath.open("wb") as f:
                    for chunk in response.iter_content(1024 * 512):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > MAX_BYTES:
                            raise RuntimeError("The file exceeded TERABOX_MAX_BYTES while downloading")
                        f.write(chunk)
                        now = time.monotonic()
                        if now - last_update > 4:
                            last_update = now
                            app.edit_message_text(message.chat.id, status.id, f"Downloading TeraBox media… {total / (1024 * 1024):.1f} MiB")
            caption = f"{title[:700]}\n\nDownloaded with All Downloader"
            app.send_video(message.chat.id, str(filepath), caption=caption, thumb=thumb_path, reply_to_message_id=message.id, supports_streaming=True)
        app.edit_message_text(message.chat.id, status.id, "TeraBox download sent successfully.")
    except Exception as exc:
        try:
            app.edit_message_text(message.chat.id, status.id, f"TeraBox download failed: {str(exc)[:500]}")
        except Exception:
            pass
    return True
