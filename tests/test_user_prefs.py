import pytest
from HELPERS import app_instance


class _FakeApp:
    def on_message(self, *a, **k):
        return lambda f: f
    on_callback_query = on_chat_member_updated = on_message


app_instance.set_app(_FakeApp())
from COMMANDS import user_prefs as up, simple_ui as ui  # noqa: E402
from ADAPTERS import terabox as tb  # noqa: E402


@pytest.fixture
def store(monkeypatch):
    state = {}
    monkeypatch.setattr(up, "state_get", lambda k, d=None: state.get(k, d))
    monkeypatch.setattr(up, "state_set", lambda k, v: state.__setitem__(k, v))
    return state


def test_quality_platform_beats_all(store):
    assert up.quality(1, "youtube") is None              # nothing set: show the menu
    up.set_quality(1, "all", "720")
    assert up.quality(1, "youtube") == "720" and up.quality(1, "instagram") == "720"
    up.set_quality(1, "youtube", "1080")
    assert up.quality(1, "youtube") == "1080"
    up.set_quality(1, "youtube", "-")                    # back to "same as All Platforms"
    assert up.quality(1, "youtube") == "720"
    up.set_quality(1, "all", "ask")
    assert up.quality(1, "youtube") is None and store["prefs:1"]["quality"] == {}
    up.set_quality(1, "all", "a")
    assert up.quality(1, "terabox") is None              # MP3 makes no sense for TeraBox: ask there


def test_thumb_scope_and_cache_guard(store):
    assert not up.has_thumb_for_url(2, "https://youtu.be/dQw4w9WgXcQ")
    up.set_thumb(2, "instagram", "FID")
    assert up.has_thumb_for_url(2, "https://www.instagram.com/reel/x/")
    assert not up.has_thumb_for_url(2, "https://youtu.be/dQw4w9WgXcQ")
    up.set_thumb(2, "all", "FID2")
    assert up.has_thumb_for_url(2, "https://youtu.be/dQw4w9WgXcQ")
    up.set_thumb(2, "all", None)
    assert store["prefs:2"]["thumb"] == {"instagram": "FID"}


def test_pref_choice_steps_down_to_what_fits():
    picks = {1080: (None, 9e9, False), 720: ("22", 1, True), 360: ("18", 1, True)}
    assert ui._pref_choice("1080", picks) == "720"       # 1080 too big -> next lower that fits
    assert ui._pref_choice("480", picks) == "360"
    assert ui._pref_choice("144", picks) == "360"        # nothing lower: smallest that fits
    assert ui._pref_choice("b", picks) == "b" and ui._pref_choice("a", picks) == "a"
    assert ui._pref_choice("720", None) == "720"         # no sizes known: format_for() caps it
    assert ui._pref_choice("b", {1080: (None, 9e9, False)}) is None   # nothing fits: show the menu


def test_terabox_default_quality(store, monkeypatch):
    from types import SimpleNamespace as NS
    msg = NS(from_user=NS(id=3), chat=NS(id=3))
    meta = {"video": True, "available": {"480", "720"}}
    assert tb._default_quality(msg, meta) is None
    up.set_quality(3, "terabox", "1080")
    assert tb._default_quality(msg, meta) == "720"
    up.set_quality(3, "terabox", "b")
    assert tb._default_quality(msg, meta) == "o"


def test_screens_render(store):
    up.set_thumb(4, "youtube", "F")
    up.set_quality(4, "all", "720")
    for text, kb in (up.home_view(4), up.thumb_view(4), up.thumb_scope_view(4, "youtube"),
                     up.thumb_scope_view(4, "all"), up.quality_view(4), up.quality_scope_view(4, "terabox"),
                     up.quality_scope_view(4, "all")):
        assert text and kb.inline_keyboard
        assert all(len(b.callback_data or "") <= 64 for row in kb.inline_keyboard for b in row)
    assert "720p" in up.home_view(4)[0] and "YouTube" in up.home_view(4)[0]


def test_thumbnail_files_fit_telegram(tmp_path):
    from PIL import Image
    src = tmp_path / "big.png"
    Image.effect_noise((2000, 1200), 100).convert("RGB").save(src)
    thumb, cover = tmp_path / "t.jpg", tmp_path / "c.jpg"
    up._make_files(str(src), str(thumb), str(cover))
    with Image.open(thumb) as t, Image.open(cover) as c:
        assert max(t.size) <= 320 and max(c.size) <= 1280
    assert thumb.stat().st_size < 200 * 1024
