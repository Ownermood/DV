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
    from ADAPTERS import terabox as tb
    monkeypatch.setattr(tb, "_load_cookies", lambda: {})
    import pytest
    with pytest.raises(RuntimeError):
        tb._native_resolve("https://terabox.com/s/1AbC")
