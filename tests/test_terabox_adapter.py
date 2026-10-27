from ADAPTERS.terabox import is_terabox_url, _extract_direct_link


def test_recognizes_supported_terabox_hosts():
    assert is_terabox_url("https://www.terabox.com/s/abc")
    assert is_terabox_url("https://1024terabox.com/sharing/link")


def test_rejects_lookalike_and_non_http_urls():
    assert not is_terabox_url("https://terabox.com.attacker.example/s/abc")
    assert not is_terabox_url("javascript://terabox.com")
    assert not is_terabox_url("https://example.com/terabox.com")


def test_extracts_common_resolution_payload():
    direct, thumb, title = _extract_direct_link({"response": [{"resolutions": {"HD Video": "https://cdn.example/video.mp4"}, "thumb": "https://cdn.example/thumb.jpg", "file_name": "video.mp4"}]})
    assert direct == "https://cdn.example/video.mp4"
    assert thumb == "https://cdn.example/thumb.jpg"
    assert title == "video.mp4"


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
