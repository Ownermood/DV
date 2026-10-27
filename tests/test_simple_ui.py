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
    ("https://www.facebook.com/watch/?v=1", "facebook"),
    ("https://fb.watch/abc/", "facebook"),
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
    "https://twitter.com/u/status/1", "https://vm.tiktok.com/ZMabc/", "https://x.com/u/status/1", "https://notfacebook.com/watch/?v=1",
    "https://evil-youtube.com/watch?v=1", "https://youtube.com.evil.io/x", "https://notinstagram.com/p/1",

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
    assert [c for c, _ in ui.BOT_MENU] == ["start", "help"]


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
    assert list(ui_kit.T) == ["en"]  # English only
    for table in ui_kit.T.values():
        for k in table:
            ui_kit.t(None, k, name="n", platform="p", title="t", max_gb="4", platforms="x", state="s")


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


def test_selected_buttons_green_premium_single_and_multi(monkeypatch):
    import asyncio
    from pyrogram import enums
    from pyrogram.types import InlineKeyboardButton
    from COMMANDS import ui_kit
    monkeypatch.setattr(ui_kit, "premium_on", lambda: True)
    monkeypatch.setattr(ui_kit, "state_get", lambda k, d=None: True if k == "premium_ok" else d)
    btns = [InlineKeyboardButton(t, callback_data="x") for t in ("✅ AVC", "✅ AV1", "☑️ VP9", "Close")]
    raws = [asyncio.run(b.write(None)) for b in btns]
    assert [b.style for b in btns] == [enums.ButtonStyle.SUCCESS, enums.ButtonStyle.SUCCESS,
                                       enums.ButtonStyle.PRIMARY, enums.ButtonStyle.DEFAULT]
    assert [b.text for b in btns] == ["AVC", "AV1", "VP9", "Close"]
    assert raws[0].style.bg_success and raws[0].style.icon == int(ui_kit.E["ok"][1])
    assert raws[2].style.bg_primary and raws[2].style.icon is None
    # plain mode keeps the tick in the text, colour still applies
    monkeypatch.setattr(ui_kit, "premium_on", lambda: False)
    b = InlineKeyboardButton("✅ MP4", callback_data="x")
    asyncio.run(b.write(None))
    assert b.text == "✅ MP4" and b.style == enums.ButtonStyle.SUCCESS and not b.icon_custom_emoji_id


def test_activity_counts(tmp_path, monkeypatch):
    from COMMANDS import activity
    monkeypatch.setattr(activity, "_FILE", str(tmp_path / "a.json"))
    monkeypatch.setattr(activity, "_data", None)
    activity.touch(1)
    activity.download(2)
    activity.download(2)
    s = activity.snapshot()
    assert s["active"][0] == 2 and s["dl"][0] == 2 and s["dl"][3] == 2
    activity.flush_now()
    assert (tmp_path / "a.json").exists()


def test_old_broadcast_records_are_converted(monkeypatch):
    from COMMANDS import admin_panel as ap
    monkeypatch.setattr(ap, "state_get", lambda k, d=None: [{"id": "x", "ts": 1, "sent": {"5": 9}}] if k == "broadcasts" else d)
    r = ap._records()[0]
    assert r["items"] == [[5, [9]]] and r["sent"] == 1 and r["deleted"] is False


def test_menus_public_and_admin_only(monkeypatch):
    from COMMANDS import admin_panel as ap
    calls = []

    class C:
        def set_bot_commands(self, cmds, scope=None):
            calls.append(("set", type(scope).__name__ if scope else "default", getattr(scope, "chat_id", None),
                          [c.command for c in cmds]))
        def delete_bot_commands(self, scope=None):
            calls.append(("del", type(scope).__name__, getattr(scope, "chat_id", None)))
    monkeypatch.setattr(ui, "_menu_set", False)
    monkeypatch.setattr(ap.Config, "ADMIN", [42], raising=False)
    ui._ensure_menu(C())
    deleted = {c[1] for c in calls if c[0] == "del"}
    assert {"BotCommandScopeAllPrivateChats", "BotCommandScopeAllGroupChats", "BotCommandScopeAllChatAdministrators"} <= deleted
    public = next(c for c in calls if c[0] == "set" and c[2] is None)
    assert public[3] == ["start", "help"]                                   # users: no admin
    admin = next(c for c in calls if c[0] == "set" and c[2] == 42)
    assert "admin" in admin[3]                                              # only in the admin's own chat


def test_non_admin_cannot_open_panel(monkeypatch):
    import pyrogram
    from types import SimpleNamespace as NS
    sent = []

    class Msg(NS):
        def stop_propagation(self):
            raise pyrogram.StopPropagation

    class C:
        def send_message(self, chat, text, **k):
            sent.append(text)
            return NS(id=1)
    monkeypatch.setattr(ui, "_menu_set", True)
    monkeypatch.setattr(ui, "_register_user", lambda m: None)
    monkeypatch.setattr(ui.Config, "ADMIN", [42], raising=False)
    for cmd in ("/admin", "/broadcast", "/addadmin 5 x", "/cookie", "/check_cookie"):
        try:
            ui.front_door(C(), Msg(text=cmd, chat=NS(id=7), from_user=NS(id=7, first_name="u"), id=3))
        except pyrogram.StopPropagation:
            pass
    assert sent and all("isn't available" in s for s in sent)


def test_yt_preview_card():
    info = {"id": "x", "title": "Crush <3", "upload_date": "20260927", "duration": 19, "view_count": 310700,
            "comment_count": 46, "channel": "Emu", "channel_follower_count": 513000,
            "formats": [{"vcodec": "none", "acodec": "mp4a", "filesize": 308000}]}
    text = ui._yt_preview(info, {1080: ("1", 3.6e6, False), 720: ("2", 1.6e6, False)})
    assert "Crush &lt;3" in text and "27-09-2026" in text and "310.7K views" in text and "513K subs" in text
    assert "1. 1080p — 3.4MB" in text and "MP3 — 301KB" in text and len(text) <= 1024
    rows = ui._choice_rows(1, "t", {1080: 1, 720: 1, 480: 1, 360: 1}, is_yt=True).inline_keyboard
    assert len(rows[0]) == 3 and "1080p • 1B" in rows[0][0].text and rows[0][0].style == ui.enums.ButtonStyle.DEFAULT
