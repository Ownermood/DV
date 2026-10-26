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
        {"height": 1080, "vcodec": "avc1", "acodec": "none", "filesize": 1000},
        {"height": 720, "vcodec": "avc1", "acodec": "none", "filesize_approx": 500},
        {"height": 360, "vcodec": "avc1", "acodec": "mp4a", "filesize": 50},
    ]}
    assert ui._yt_sizes(info) == {1080: 1010, 720: 510, 360: 50}


def test_quoted_caption_single_expandable_block():
    from HELPERS.caption import build_quoted_caption
    c = build_quoted_caption("<b>T</b>", "", "desc", "#tag\n", '<a href="u">L</a>')
    assert c.startswith("<blockquote expandable><b>T</b>") and c.endswith("</a></blockquote>")
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
