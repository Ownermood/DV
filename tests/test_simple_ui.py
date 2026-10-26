import pytest
from HELPERS import app_instance


class _FakeApp:
    def on_message(self, *a, **k):
        return lambda f: f
    on_callback_query = on_message


app_instance.set_app(_FakeApp())
from COMMANDS import simple_ui as ui  # noqa: E402


@pytest.mark.parametrize("text,key", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube"),
    ("watch this https://youtu.be/dQw4w9WgXcQ?t=5 pls", "youtube"),
    ("https://m.youtube.com/shorts/abc", "youtube"),
    ("https://vm.tiktok.com/ZMabc/", "tiktok"),
    ("https://www.tiktok.com/@u/video/7123", "tiktok"),
    ("https://www.snapchat.com/spotlight/W7_abc", "snapchat"),
    ("https://www.snapchat.com/add/user", "snapchat"),
    ("https://in.pinterest.com/pin/123/", "pinterest"),
    ("https://pin.it/4abcd", "pinterest"),
    ("https://terabox.com/s/1AbCdEf", "terabox"),
    ("https://www.1024terabox.com/s/1AbC", "terabox"),
    ("https://teraboxapp.com/s/1AbC", "terabox"),
])
def test_supported_detected(text, key):
    assert ui.detect_platform(text)[0] == key


@pytest.mark.parametrize("text", [
    "https://twitter.com/u/status/1", "https://x.com/u/status/1", "https://www.facebook.com/watch/?v=1",
    "https://evil-youtube.com/watch?v=1", "https://youtube.com.evil.io/x", "https://notinstagram.com/p/1",
    "https://www.instagram.com/reel/C0abc/",
    "hello there", "", "https://example.com/?u=https://youtube.com/watch?v=1",
])
def test_unsupported_rejected(text):
    assert ui.detect_platform(text) is None


def test_trailing_punctuation_and_text():
    assert ui.detect_platform("(https://youtu.be/abc).")[3] == "https://youtu.be/abc"


def test_welcome_escapes_name_and_lists_platforms():
    assert "&lt;b&gt;" in ui.welcome_text("<b>Evil</b>")
    assert all(l in ui.help_text() for l, _, _ in ui.PLATFORMS.values())


def test_menu_is_minimal():
    assert [c for c, _ in ui.BOT_MENU] == ["start", "help", "lang"]


def test_format_for_caps_size_and_height(monkeypatch):
    monkeypatch.setattr(ui.Config, "MAX_FILE_SIZE_GB", 4, raising=False)
    f = ui.format_for(None)
    assert "[filesize<?3891M]" in f and "height" not in f and f.endswith("/b")
    f = ui.format_for(720)
    assert f.startswith("bv*[height<=720][filesize<?3891M]") and f.endswith("/b[height<=720]")


def test_yt_sizes_adds_audio_and_buckets():
    info = {"formats": [
        {"vcodec": "none", "acodec": "opus", "filesize": 10},
        {"format_id": "1", "height": 1080, "vcodec": "avc1", "acodec": "none", "filesize": 1000},
        {"format_id": "2", "height": 720, "vcodec": "avc1", "acodec": "none", "filesize_approx": 500},
        {"format_id": "3", "height": 360, "vcodec": "avc1", "acodec": "mp4a", "filesize": 50},
    ]}
    assert ui._yt_sizes(info) == {1080: 1010, 720: 510, 360: 50}


def test_quoted_caption_single_expandable_block():
    from HELPERS.caption import build_quoted_caption
    c = build_quoted_caption("<b>T</b>", "", "desc", "#tag\n", '<a href="u">L</a>')
    assert c.startswith("<blockquote expandable><b>T") and c.endswith("</a></b></blockquote>")
    assert c.count("<blockquote") == 1


def test_buttons_premium_and_plain(monkeypatch):
    from COMMANDS import ui_kit
    monkeypatch.setattr(ui_kit, "premium_on", lambda: True)
    b = ui_kit.btn("music", "MP3", cb="x", style=ui_kit.GREEN)
    assert b.icon_custom_emoji_id and b.text == "MP3" and b.style == ui_kit.GREEN
    monkeypatch.setattr(ui_kit, "premium_on", lambda: False)
    b = ui_kit.btn("music", "MP3", cb="x")
    assert b.text == "🎧 MP3" and not b.icon_custom_emoji_id


def test_all_i18n_keys_format():
    from COMMANDS import ui_kit
    for lang, table in ui_kit.T.items():
        assert set(table) == set(ui_kit.T["en"]), lang
        for k in table:
            ui_kit.t(None, k, name="n", platform="p", title="t", max_gb="4", platforms="x")


def test_bold_everywhere_including_quotes():
    from COMMANDS.ui_kit import bold
    out = bold("Hi <b>x</b>\n<blockquote expandable>line</blockquote>\nend")
    assert out == "<b>Hi x</b>\n<blockquote expandable><b>line</b></blockquote>\n<b>end</b>"


def test_friendly_errors():
    from HELPERS.logger import friendly_error
    assert "private" in friendly_error("ERROR: [youtube] x: Private video. Sign in if you've been granted access")
    assert "login check" in friendly_error("ERROR: Sign in to confirm you're not a bot")
    assert "unavailable" in friendly_error("❌ Unknown error: Video unavailable")
    assert "Download failed" in friendly_error("❌ Unknown error: something weird")
    assert friendly_error("✅ Cookies saved, private links work now") == "✅ Cookies saved, private links work now"


def test_yt_pick_exact_4gb_limit(monkeypatch):
    monkeypatch.setattr(ui.Config, "MAX_FILE_SIZE_GB", 4, raising=False)
    G = 1024 ** 3
    info = {"formats": [
        {"format_id": "a", "vcodec": "none", "acodec": "opus", "filesize": int(0.1 * G)},
        {"format_id": "av1", "height": 1080, "vcodec": "av01", "acodec": "none", "filesize": int(4.2 * G)},
        {"format_id": "h264", "height": 1080, "vcodec": "avc1", "acodec": "none", "filesize": int(3.85 * G)},
        {"format_id": "big", "height": 2160, "vcodec": "vp9", "acodec": "none", "filesize": int(3.95 * G)},
    ]}
    p = ui._yt_pick(info)
    assert p[1080][0] == "h264"            # 3.95 GB total fits, av1 (4.3) does not
    assert p[2160] == (None, int(3.95 * G) + int(0.1 * G), False)  # 4.05 GB > 4 GB


def test_local_shared_cookie_source(tmp_path):
    from COMMANDS.cookies_cmd import _download_content
    f = tmp_path / "youtube.txt"
    f.write_text("# Netscape HTTP Cookie File\n")
    ok, status, data, err = _download_content(str(f))
    assert ok and status == 200 and data.startswith(b"# Netscape")
    assert _download_content(str(tmp_path / "missing.txt"))[0] is False
