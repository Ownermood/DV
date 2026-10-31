import pytest
from HELPERS import app_instance


class _FakeApp:
    def on_message(self, *a, **k):
        return lambda f: f
    on_callback_query = on_chat_member_updated = on_message


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
    assert f.startswith("bv*[aspect_ratio<1][width<=720][filesize<?3891M]")       # reels: 720 wide
    assert "+ba/bv*[height<=720][filesize<?3891M]" in f                           # landscape: 720 high
    assert f.endswith("/b")   # never "Requested format is not available"


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
            ui_kit.t(None, k, name="n", platform="p", title="t", max_gb="4", platforms="x", state="s", group="g", me="m", channel="c", url="u")


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


def test_many_links_run_one_by_one(monkeypatch):
    import threading
    import types
    done, sent = [], []
    finished = threading.Event()

    def fake_run(client, m, choice, picks=None):
        done.append((m.text, choice))
        if len(done) == 2:
            finished.set()
    monkeypatch.setattr(ui, "_run_choice", fake_run)
    monkeypatch.setattr(ui, "send", lambda c, uid, build, **k: sent.append(build()[0]))
    monkeypatch.setattr(ui.activity, "download", lambda uid: None)
    msg = types.SimpleNamespace(chat=types.SimpleNamespace(id=7), id=1, text="")
    ui._queue_links(None, msg, ["https://youtu.be/dQw4w9WgXcQ", "https://example.com/x", "https://pin.it/4abcd"])
    assert finished.wait(5)
    assert done == [("https://youtu.be/dQw4w9WgXcQ", "b"), ("https://pin.it/4abcd", "b")]
    assert "2 link(s) added" in sent[0] and "1 unsupported" in sent[0]


def test_preview_other_platform():
    info = {"id": "abc", "description": "Reel text", "like_count": 1200, "uploader": "someone",
            "formats": [{"vcodec": "h264", "acodec": "aac", "height": 720, "filesize": 5 * 1024 ** 2, "format_id": "x"}]}
    text = ui._yt_preview(info, ui._yt_pick(info), "Instagram")
    assert "Reel text" in text and "1.2K" in text and "someone" in text and "720p — 5.0MB" in text


def test_incomplete_youtube_link():
    assert ui._incomplete_youtube("https://www.youtube.com/watch?v=jrbRbXX8R9")
    assert not ui._incomplete_youtube("https://youtu.be/dQw4w9WgXcQ?si=x")
    assert not ui._incomplete_youtube("https://youtube.com/shorts/61AaTfgy1-k")
    assert not ui._incomplete_youtube("https://www.youtube.com/playlist?list=PL123")


def _gmsg(text, reply=None):
    from types import SimpleNamespace as NS
    return NS(text=text, reply_to_message=NS(text=reply, caption=None) if reply else None)


@pytest.mark.parametrize("text,reply,want", [
    ("/dl https://youtu.be/dQw4w9WgXcQ", None, "https://youtu.be/dQw4w9WgXcQ"),
    ("/dl@Downloader_Villanbot https://youtu.be/dQw4w9WgXcQ", None, "https://youtu.be/dQw4w9WgXcQ"),
    ("@Downloader_Villanbot https://pin.it/4abcd", None, "https://pin.it/4abcd"),
    ("/dl", "look https://pin.it/4abcd", "https://pin.it/4abcd"),
    ("/dl", None, ""),
    ("https://youtu.be/dQw4w9WgXcQ", None, None),           # plain chat in the group: ignored
    ("/dl@OtherBot https://pin.it/4abcd", None, None),       # another bot's command
])
def test_group_link(monkeypatch, text, reply, want):
    monkeypatch.setattr(ui.Config, "BOT_NAME", "Downloader_Villanbot", raising=False)
    assert ui._group_link(None, _gmsg(text, reply)) == want


def test_owner_of_prefers_sender():
    from types import SimpleNamespace as NS
    assert ui._owner_of(NS(from_user=NS(id=5), chat=NS(id=-100))) == 5
    assert ui._owner_of(NS(from_user=None, chat=NS(id=7))) == 7


def test_group_greeting_and_add_button(monkeypatch):
    monkeypatch.setattr(ui.Config, "BOT_NAME", "Downloader_Villanbot", raising=False)
    txt = ui.group_text("<Fans>")
    assert "&lt;Fans&gt;" in txt and "@Downloader_Villanbot" in txt and "/dl" in txt
    assert ui._add_group_url() == "https://t.me/Downloader_Villanbot?startgroup=true"
    urls = [b.url for row in ui._start_markup(1).inline_keyboard for b in row]
    assert ui._add_group_url() in urls
    assert [c for c, _ in ui.GROUP_MENU] == ["dl", "help"]


def test_group_needs_bot_admin(monkeypatch):
    from types import SimpleNamespace as NS
    from pyrogram import enums
    calls, sent = [], []

    class C:
        def get_chat_member(self, chat, who):
            calls.append(chat)
            return NS(status=enums.ChatMemberStatus.MEMBER)
    ui._bot_admin.clear()
    assert ui._is_bot_admin(C(), -5) is False and ui._is_bot_admin(C(), -5) is False
    assert calls == [-5]                                    # cached
    monkeypatch.setattr(ui, "send", lambda c, chat, build, **k: sent.append(build()[0]))
    upd = NS(chat=NS(id=-5), new_chat_member=NS(user=NS(is_self=True), status=enums.ChatMemberStatus.ADMINISTRATOR),
             old_chat_member=NS(status=enums.ChatMemberStatus.MEMBER))
    ui.bot_promoted(C(), upd)
    assert ui._is_bot_admin(C(), -5) is True and calls == [-5]
    assert len(sent) == 1 and "admin" in sent[0]
    assert "admin" in ui.t(None, "need_admin")


class _Client:
    def __init__(self, can_dm=True):
        self.can_dm, self.sent, self.deleted = can_dm, [], []

    def send_message(self, chat, text, **kw):
        if chat > 0 and not self.can_dm:
            raise RuntimeError("PEER_ID_INVALID")
        from types import SimpleNamespace as NS
        self.sent.append((chat, text))
        return NS(id=len(self.sent), chat=NS(id=chat), text=text)

    def delete_messages(self, chat, ids):
        self.deleted.append((chat, ids))


def _gm(person, chat=-100, mid=5):
    from types import SimpleNamespace as NS
    return NS(id=mid, chat=NS(id=chat, title="Fans"), from_user=person, text="/dl x",
              message_thread_id=None, is_topic_message=False)


def test_group_request_gets_dm_twin():
    from types import SimpleNamespace as NS
    person = NS(id=77, username=None, first_name="Ann")
    c = _Client()
    dm = ui._make_twin(c, _gm(person), "https://youtu.be/dQw4w9WgXcQ")
    assert dm.chat.id == 77 and dm.from_user is person and dm.text == "https://youtu.be/dQw4w9WgXcQ"
    assert "Fans" in c.sent[0][1] and "Pick the quality in the group" in c.sent[0][1]
    assert ui._make_twin(_Client(can_dm=False), _gm(person), "https://pin.it/4abcd") is None   # never started


def test_start_button_brings_the_menu_to_the_group(monkeypatch):
    from types import SimpleNamespace as NS
    runs = []
    monkeypatch.setattr(ui, "_run_link", lambda c, m, u: runs.append((m.chat.id, m._dm_twin.chat.id, u)))
    person = NS(id=78, username="bob", first_name="Bob")
    ui._DM_WAIT["tok"] = (78, "https://pin.it/4abcd", _gm(person), __import__("time").time(), 9)
    assert ui._resume_from_group(_Client(), NS(id=99), "tok") is False      # someone else's button
    c = _Client()
    assert ui._resume_from_group(c, person, "tok") is True
    assert runs == [(-100, 78, "https://pin.it/4abcd")]                      # menu in the group, job in the DM
    assert c.deleted == [(-100, 9)] and "tok" not in ui._DM_WAIT              # prompt gone, one use


def test_choice_runs_on_the_dm_twin(monkeypatch):
    from types import SimpleNamespace as NS
    import URL_PARSERS.url_extractor as ux
    from HELPERS import group_mirror
    seen = []
    monkeypatch.setattr(ux, "url_distractor", lambda c, m: seen.append((m.chat.id, group_mirror.active(m.chat.id) is not None)))
    monkeypatch.setattr(ui, "_after_markup", lambda m: None)
    person = NS(id=79, username=None, first_name="C")
    gm = _gm(person)
    gm._dm_twin = NS(id=1, chat=NS(id=79), from_user=person, text="https://youtu.be/dQw4w9WgXcQ")
    ui._run_choice(_Client(), gm, "b")
    assert seen == [(79, True)]                        # downloaded in the DM, mirrored while it ran
    assert group_mirror.active(79) is None             # mirror ends with the job


def test_group_messages_deleted_later(monkeypatch):
    import time
    monkeypatch.setattr(ui, "GROUP_CLEAN_AFTER", 0.05)
    c = _Client()
    ui._delete_later(c, -100, [5, None, 6])
    time.sleep(0.3)
    assert c.deleted == [(-100, [5, 6])]


def test_mention_and_dm_buttons(monkeypatch):
    from types import SimpleNamespace as NS
    monkeypatch.setattr(ui.Config, "BOT_NAME", "Downloader_Villanbot", raising=False)
    assert ui._mention(NS(id=1, username="ann", first_name="A")) == "@ann"
    assert 'tg://user?id=2' in ui._mention(NS(id=2, username=None, first_name="<B>"))
    url = ui._dm_markup("dlabc").inline_keyboard[0][0].url
    assert url == "https://t.me/Downloader_Villanbot?start=dlabc" and len("dlabc") <= 64


def test_forum_topic_answers_stay_in_topic():
    from types import SimpleNamespace as NS
    assert ui._topic(NS(message_thread_id=42, is_topic_message=True)) == {"message_thread_id": 42}
    assert ui._topic(NS(message_thread_id=None, is_topic_message=False)) == {}   # General / normal group
    assert ui._topic(NS(message_thread_id=9, is_topic_message=False)) == {}      # reply thread, not a topic


def test_unused_group_menu_goes_after_30s(monkeypatch):
    import time
    from types import SimpleNamespace as NS
    monkeypatch.setattr(ui, "GROUP_MENU_TTL", 0.05)
    c = _Client()
    gm = NS(id=5, chat=NS(id=-100))
    ui._PENDING["unused"] = (gm, "youtube", time.time())
    ui._PENDING["picked"] = (gm, "youtube", time.time())
    ui._expire_group_menu(c, gm, "unused", 11)
    ui._expire_group_menu(c, gm, "picked", 12)
    ui._PENDING.pop("picked")                       # the member chose a quality in time
    ui._expire_group_menu(c, NS(id=1, chat=NS(id=55)), "dm", 13)   # private chats: never
    time.sleep(0.3)
    assert c.deleted == [(-100, [11, 5])] and "unused" not in ui._PENDING


@pytest.mark.parametrize("url,ok", [
    ("https://www.instagram.com/reel/Dd8AD0LP4vg/?stkn=x", True),
    ("https://www.instagram.com/p/C0abc/", True),
    ("https://www.instagram.com/someone/reel/C0abc/", True),
    ("https://www.instagram.com/stories/someone/123/", True),
    ("https://www.instagram.com/share/reel/abc/", True),
    ("https://instagr.am/p/C0abc/", True),
    ("https://www.instagram.com/api/v1/media/4013602588675238339/info/", False),
    ("https://www.instagram.com/someone/", False),
    ("https://www.instagram.com/explore/", False),
])
def test_instagram_only_single_posts(url, ok):
    assert (ui.detect_platform(url) is not None) is ok


def test_image_command_runs_once_per_link(monkeypatch):
    import threading, time
    from types import SimpleNamespace as NS
    from COMMANDS import image_cmd
    calls, gate = [], threading.Event()
    monkeypatch.setattr(image_cmd, "_image_command", lambda app, m: (calls.append(m.text), gate.wait(1)))
    m = NS(chat=NS(id=5), text="/img https://www.instagram.com/p/x/")
    t = threading.Thread(target=image_cmd.image_command, args=(None, m))
    t.start()
    time.sleep(0.1)
    image_cmd.image_command(None, m)          # same link while the first runs: dropped
    gate.set()
    t.join()
    image_cmd.image_command(None, m)          # after it finished: runs again
    assert len(calls) == 2
    assert "Nothing to download" in image_cmd._img_done_text(1, 0, 1000, 0)


def test_own_and_bot_messages_are_ignored():
    import asyncio
    from types import SimpleNamespace as NS
    from pyrogram import filters, StopPropagation
    f = filters.me | filters.bot
    own = NS(from_user=NS(is_self=True, is_bot=True), outgoing=True)
    echoed = NS(from_user=NS(is_self=False, is_bot=True), outgoing=False)   # same bot from another session
    person = NS(from_user=NS(is_self=False, is_bot=False), outgoing=False)
    run = lambda m: asyncio.run(f(None, m))
    assert run(own) and run(echoed) and not run(person)
    with pytest.raises(StopPropagation):
        ui.ignore_own_messages(None, NS(stop_propagation=lambda: (_ for _ in ()).throw(StopPropagation())))


def test_safe_delete_of_gone_message_does_not_crash(monkeypatch):
    from HELPERS import safe_messeger as sm
    from CONFIG.messages import safe_get_messages
    gone = safe_get_messages(None).HELPER_MESSAGE_ID_INVALID_MSG

    class App:
        def delete_messages(self, **k):
            raise RuntimeError(gone)
    monkeypatch.setattr(sm, "get_app_safe", lambda: App())
    assert sm.safe_delete_messages(chat_id=5, message_ids=[1]) is None     # was NameError: user_id


def test_join_is_verified_without_a_tap(monkeypatch):
    import time
    from types import SimpleNamespace as NS
    from HELPERS import limitter
    runs, joined = [], {"yes": False}
    monkeypatch.setattr(ui, "_run_link", lambda c, m, u: runs.append(u))
    monkeypatch.setattr(ui, "JOIN_POLL", 0.05)
    monkeypatch.setattr(limitter, "is_member", lambda c, uid: joined["yes"])
    c = _Client()
    msg = NS(id=3, chat=NS(id=42), from_user=NS(id=42), text="https://youtu.be/dQw4w9WgXcQ")
    ui.join_card(c, msg)
    time.sleep(0.15)
    assert runs == []                                    # not joined yet: waits
    joined["yes"] = True
    time.sleep(0.2)
    assert runs == ["https://youtu.be/dQw4w9WgXcQ"]      # joined: the link starts by itself
    assert c.deleted and c.deleted[0][0] == 42           # and the join card is gone


def test_channel_join_update_verifies_at_once(monkeypatch):
    from types import SimpleNamespace as NS
    from pyrogram import enums
    runs = []
    monkeypatch.setattr(ui, "_run_link", lambda c, m, u: runs.append(u))
    monkeypatch.setattr(ui.Config, "SUBSCRIBE_CHANNEL", -100555, raising=False)
    msg = NS(id=3, chat=NS(id=43), from_user=NS(id=43), text="x")
    ui._JOIN["tk"] = (msg, "https://pin.it/4abcd", __import__("time").time(), None)
    upd = NS(chat=NS(id=-100555), new_chat_member=NS(status=enums.ChatMemberStatus.MEMBER, user=NS(id=43)))
    ui.channel_joined(_Client(), upd)
    assert runs == ["https://pin.it/4abcd"] and "tk" not in ui._JOIN
