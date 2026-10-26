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
    ("https://www.instagram.com/reel/C0abc/", "instagram"),
    ("https://instagram.com/p/xyz/?igsh=1", "instagram"),
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
    "hello there", "", "https://example.com/?u=https://youtube.com/watch?v=1",
])
def test_unsupported_rejected(text):
    assert ui.detect_platform(text) is None


def test_trailing_punctuation_and_text():
    assert ui.detect_platform("(https://youtu.be/abc).")[3] == "https://youtu.be/abc"


def test_welcome_escapes_name_and_lists_platforms():
    t = ui.welcome_text("<b>Evil</b>")
    assert "&lt;b&gt;" in t and all(l in t for l, _, _ in ui.PLATFORMS.values())


def test_menu_is_minimal():
    assert [c for c, _ in ui.BOT_MENU] == ["start", "help", "cookie"]
