from ADAPTERS.terabox import is_terabox_url


def test_recognizes_supported_terabox_hosts():
    assert is_terabox_url("https://www.terabox.com/s/abc")
    assert is_terabox_url("https://1024terabox.com/sharing/link")


def test_rejects_lookalike_and_non_http_urls():
    assert not is_terabox_url("https://terabox.com.attacker.example/s/abc")
    assert not is_terabox_url("javascript://terabox.com")
    assert not is_terabox_url("https://example.com/terabox.com")


def test_native_resolver_flow(monkeypatch):
    from ADAPTERS import terabox as tb

    class Resp:
        def __init__(self, text="", url="", js=None):
            self.text, self.url, self._js = text, url, js
        def raise_for_status(self):
            pass
        def json(self):
            return self._js

    calls = []

    class Sess:
        def __init__(self):
            self.headers, self.cookies = {}, type("C", (), {"update": lambda s, d: None, "get_dict": lambda s: {"ndus": "x"}})()
        def get(self, url, params=None, **k):
            calls.append((url, params))
            if "share/list" in url:
                return Resp(js={"errno": 0, "list": [{"isdir": "0", "size": "1000", "dlink": "https://d.terabox.com/f",
                                                    "server_filename": "clip.mp4", "thumbs": {"url3": "https://t/x.jpg"}}]})
            return Resp(text='x fn%28%22TOKEN123%22%29 y dp-logid=987&z',
                        url="https://www.terabox.com/sharing/link?surl=AbC")

    monkeypatch.setattr(tb, "_load_cookies", lambda: {"ndus": "x"})
    monkeypatch.setattr(tb.requests, "Session", Sess)
    direct, thumb, title, size, cookies = tb._native_resolve("https://terabox.com/s/1AbC")
    assert direct == "https://d.terabox.com/f" and title == "clip.mp4" and size == 1000 and thumb == "https://t/x.jpg"
    params = calls[1][1]
    assert calls[1][0] == "https://www.terabox.com/share/list"
    assert params["jsToken"] == "TOKEN123" and params["shorturl"] == "AbC" and params["dplogid"] == "987"


def test_native_resolver_needs_cookie(monkeypatch):
    """Without a login TeraBox lists the file but gives no dlink -> NeedCookie with name and size."""
    import pytest
    from ADAPTERS import terabox as tb

    class Resp:
        def __init__(self, text="", url="", js=None):
            self.text, self.url, self._js = text, url, js
        def raise_for_status(self):
            pass
        def json(self):
            return self._js

    class Sess:
        def __init__(self):
            self.headers, self.cookies = {}, type("C", (), {"update": lambda s, d: None})()
        def get(self, url, params=None, **k):
            if "share/list" in url:
                return Resp(js={"errno": 0, "list": [{"isdir": "0", "size": "3196799587", "server_filename": "movie.mkv"}]})
            return Resp(text="fn%28%22T%22%29 dp-logid=1&", url="https://www.1024tera.com/sharing/link?surl=X")

    monkeypatch.setattr(tb, "_load_cookies", lambda: {})
    monkeypatch.setattr(tb.requests, "Session", Sess)
    with pytest.raises(tb.NeedCookie) as e:
        tb._native_resolve("https://terasharelink.com/s/1X")
    assert e.value.title == "movie.mkv" and e.value.size == 3196799587


def test_mirror_domains_and_lookalikes():
    from ADAPTERS import terabox as tb
    assert tb.is_terabox_url("https://terasharelink.com/s/18Kna")
    assert tb.is_terabox_url("https://www.1024tera.com/sharing/link?surl=x")
    assert not tb.is_terabox_url("https://evilterabox.com/s/1")


def test_worker_resolver(monkeypatch):
    from ADAPTERS import terabox as tb

    class R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def json(self):
            return {"file_name": "a.mp4", "download_link": "https://d/x", "size_bytes": 5,
                    "proxy_url": "https://w.dev/proxy?url=x", "thumbnail": ""}
    sent = {}
    monkeypatch.setattr(tb, "WORKER_URL", "https://w.dev")
    monkeypatch.setattr(tb.requests, "post", lambda u, json=None, **k: (sent.update(u=u, json=json), R())[1])
    direct, thumb, title, size, cookies = tb._worker_resolve("https://terasharelink.com/s/1x")
    assert sent == {"u": "https://w.dev/", "json": {"link": "https://terasharelink.com/s/1x"}}
    assert direct == "https://w.dev/proxy?url=x" and title == "a.mp4" and size == 5 and thumb is None


def test_cache_roundtrip(monkeypatch):
    import types
    from ADAPTERS import terabox as tb
    store = {}
    import COMMANDS.ui_kit as kit
    monkeypatch.setattr(kit, "state_get", lambda k, d=None: store.get(k, d))
    monkeypatch.setattr(kit, "state_set", lambda k, v: store.__setitem__(k, v))
    url = "https://terasharelink.com/s/18KnaRqYo1qA8Smga-tgQUQ"
    assert tb._share_key(url) == "v2:8KnaRqYo1qA8Smga-tgQUQ"
    vid = types.SimpleNamespace(video=types.SimpleNamespace(file_id="F1"), document=None)
    tb._cache_put(url, [vid], "Movie.mkv")
    assert tb._cache_get("https://www.1024terabox.com/s/18KnaRqYo1qA8Smga-tgQUQ") == {"t": "Movie.mkv", "f": [("F1", "v")]}


def test_meta_has_every_key(monkeypatch):
    from ADAPTERS import terabox as tb

    class R:
        def __init__(self, url, text="", js=None):
            self.url, self.text, self._js = url, text, js
        def raise_for_status(self): pass
        def json(self): return self._js

    page = R("https://www.1024tera.com/sharing/link?surl=abc", 'x fn%28%22TOKEN%22%29 dp-logid=1&')
    lst = R("", js={"errno": 0, "uk": 1, "share_id": 2, "list": [{"isdir": "0", "category": "1", "fs_id": "9",
            "server_filename": "a.mkv", "size": "100", "width": 1920, "height": 800, "duration": 60,
            "thumbs": {"url3": "http://t"}}]})
    info = R("", js={"uk": 1, "shareid": 2, "sign": "s", "timestamp": 3})
    seq = iter([page, lst, info])
    monkeypatch.setattr(tb.requests.Session, "get", lambda self, *a, **k: next(seq))
    m = tb._meta("https://terasharelink.com/s/1abc", {})
    assert m["thumb"] == "http://t" and m["height"] == 1080 and m["title"] == "a.mkv" and m["video"]


def test_part_label_bold_on_top_outside_quote():
    from ADAPTERS import terabox
    cap = terabox._caption("My Video", "https://terabox.com/s/1x", "Part 2/3")
    assert cap.startswith("<b>🎞 Part 2/3</b>\n") and "Part 2/3" not in cap.split("\n", 1)[1]
    assert not terabox._caption("My Video", "https://terabox.com/s/1x").startswith("<b>🎞")


def test_cancelled_or_empty_share_is_a_dead_link(monkeypatch):
    import pytest
    from ADAPTERS import terabox as tb
    for errno in (-21, 2130):
        with pytest.raises(tb.DeadLink):
            tb._check_share({"errno": errno})
    with pytest.raises(RuntimeError) as other:
        tb._check_share({"errno": 9019})
    assert not isinstance(other.value, tb.DeadLink)       # unknown errors still try the other resolvers
    tb._check_share({"errno": 0})
    calls = []
    monkeypatch.setattr(tb, "_cookie_files", lambda: [])
    monkeypatch.setattr(tb, "_native_resolve", lambda url, ck: (_ for _ in ()).throw(tb.DeadLink("gone")))
    monkeypatch.setattr(tb, "_worker_resolve", lambda url: calls.append(url))
    with pytest.raises(tb.DeadLink):
        tb._resolve("https://1024terabox.com/s/1abc")
    assert calls == []                                      # no fallback for a dead share


def test_stream_progress_gives_exact_percent(monkeypatch, tmp_path):
    from ADAPTERS import terabox as tb
    sizes = [5000, 100, 100, 100]               # a big first segment made the old size guess 4x too high

    class Sess:
        headers, cookies = {}, {}

        def mount(self, *a):
            pass

        def get(self, u, timeout=None):
            return type("R", (), {"content": b"x" * sizes[int(u.rsplit("/", 1)[1])]})()
    monkeypatch.setattr(tb.requests, "Session", Sess)
    monkeypatch.setattr(tb, "PARTS", 1)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: (tb.Path(a[0][-1]).write_bytes(b"v")))
    seen = []
    tb._hls_download("\n".join(f"https://s/{i}" for i in range(4)), tmp_path / "o.mp4", 10 ** 9,
                     lambda done, guess, frac: seen.append(frac))
    assert seen == [0.25, 0.5, 0.75, 1.0]       # % follows segments, not the size guess


def test_big_video_parts_cover_the_whole_video(tmp_path):
    import shutil, subprocess
    import pytest
    from ADAPTERS import terabox as tb
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    src = tmp_path / "in.ts"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=25",
                    "-t", "60", "-c:v", "libx264", "-preset", "ultrafast", "-b:v", "1M", "-g", "25", "-f", "mpegts",
                    str(src)], check=True)
    parts = tb._split_by_size(src, int(src.stat().st_size / 2.5))
    total = sum(tb._duration(p) for p in parts)
    assert len(parts) == 3 and abs(total - 60) < 2      # parts follow each other to the end
    assert all(tb.Path(p).stat().st_size <= src.stat().st_size / 2.5 * 1.02 for p in parts)


def test_slow_server_resumes_instead_of_failing(monkeypatch, tmp_path):
    import requests
    from ADAPTERS import terabox as tb
    data = bytes(range(256)) * 40000            # ~10 MB, 2 parts
    calls = {"stalled": False, "ranges": []}

    class R:
        def __init__(self, a, b):
            self.status_code, self.headers, self.url = 206, {"Content-Range": f"bytes {a}-{b}/{len(data)}"}, "https://x"
            self.a, self.b = a, b

        def iter_content(self, n):
            if self.a > 0 and not calls["stalled"]:          # second part: 1 MB, then the server stalls
                yield data[self.a:self.a + 1024 * 1024]
                calls["stalled"] = True
                raise requests.exceptions.ReadTimeout("Read timed out. (read timeout=10.0)")
            yield data[self.a:self.b + 1]

        def close(self):
            pass
        __enter__ = lambda self: self
        __exit__ = lambda self, *a: None

    class Sess:
        headers, cookies = {}, {}

        def mount(self, *a):
            pass

        def get(self, u, headers=None, **k):
            a, b = headers["Range"][6:].split("-")
            a, b = int(a), int(b or len(data) - 1)
            calls["ranges"].append((a, b))
            return R(a, b)
    monkeypatch.setattr(tb.requests, "Session", Sess)
    monkeypatch.setattr(tb, "PARTS", 2)
    monkeypatch.setattr(tb.time, "sleep", lambda s: None)
    out = tmp_path / "f.bin"
    tb._fast_download("https://x", out, {}, len(data), 10 ** 9, lambda *a: None)
    assert out.read_bytes() == data                          # complete and correct after the stall
    half = len(data) // 2
    assert (half + 1024 * 1024, len(data) - 1) in calls["ranges"]   # resumed where it stopped


def test_menu_shows_only_real_qualities_with_sizes(monkeypatch):
    from ADAPTERS import terabox as tb
    meta = {"video": True, "height": 1080, "available": {"720"}, "est": {"720": 300 * 1024 ** 2},
            "size": 2106720480}
    labels = [b.text for row in tb._menu_kb("t", meta).inline_keyboard for b in row]
    assert any("720p" in l and "300 MB" in l for l in labels)
    assert not any("480p" in l or "1080p" in l for l in labels)          # not given for this link: not shown
    assert any("Original" in l and "1.96 GB" in l for l in labels)
    meta["available"] = set()
    rows = tb._menu_kb("t", meta).inline_keyboard
    assert all(rows) and len(rows) == 2                                  # only Original + Close, no empty row


def test_stream_size_from_segments(monkeypatch):
    from ADAPTERS import terabox as tb

    class S:
        headers = {}

        def head(self, u, **k):
            return type("R", (), {"ok": True, "headers": {"Content-Length": "1000"}})()
    monkeypatch.setattr(tb.requests, "Session", S)
    pl = "#EXTM3U\n" + "\n".join(f"#EXTINF:6,\nhttps://s/{i}.ts" for i in range(30))
    assert tb._stream_size({"base": "https://x"}, pl) == 30000


def test_old_wrong_parts_are_not_reused_from_cache(monkeypatch):
    from ADAPTERS import terabox as tb
    from COMMANDS import ui_kit
    state = {"terabox_cache": {"1wO1UGR4EP09Vwar9futoA": {"t": "old", "f": [["x", "v"]]}}}   # before the fix
    monkeypatch.setattr(ui_kit, "state_get", lambda k, d=None: state.get(k, d))
    assert tb._cache_get("https://www.1024tera.com/wap/share/filelist?surl=1wO1UGR4EP09Vwar9futoA") is None


def test_broken_ts_falls_back_to_byte_parts(monkeypatch, tmp_path):
    import shutil, subprocess
    import pytest
    from ADAPTERS import terabox as tb
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    src = tmp_path / "in.ts"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=25",
                    "-t", "60", "-c:v", "libx264", "-preset", "ultrafast", "-b:v", "1M", "-g", "25", "-f", "mpegts",
                    str(src)], check=True)
    small = tmp_path / "part1.mp4"
    small.write_bytes(b"x" * 2048)
    monkeypatch.setattr(tb, "_split_ffmpeg", lambda s, l: [str(small)])   # ffmpeg stopped early (75 MB of 2 GB)
    limit = int(src.stat().st_size / 2.5)
    parts = tb._split_by_size(src, limit)
    assert len(parts) == 3 and all(p.endswith(".mp4") for p in parts)   # fake 2 KB part replaced, all playable mp4
    assert all(tb.Path(p).stat().st_size <= limit for p in parts)
    assert sum(tb._duration(p) for p in parts) > 55                     # the whole video is in the parts


def test_sent_files_go_to_log_channel(monkeypatch):
    from types import SimpleNamespace as NS
    from ADAPTERS import terabox as tb
    from HELPERS import logger as lg
    fw = []
    monkeypatch.setattr(lg, "get_log_channel", lambda kind="general", **k: -100777 if kind == "video" else 0)
    app = NS(forward_messages=lambda to, frm, ids: fw.append((to, frm, ids)))
    tb._to_log_channel(app, 55, [NS(id=1), NS(id=2)])
    assert fw == [(-100777, 55, [1, 2])]
