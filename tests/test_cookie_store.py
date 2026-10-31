import time

import pytest

from COMMANDS import cookie_store as cs, ui_kit


def _cookie(name="sessionid", value="v1", exp=None):
    exp = int(time.time()) + 30 * 86400 if exp is None else exp
    return f"# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tTRUE\t{exp}\t{name}\t{value}\n"


@pytest.fixture
def store(tmp_path, monkeypatch):
    state = {}
    monkeypatch.setattr(ui_kit, "state_get", lambda k, d=None: state.get(k, d))
    monkeypatch.setattr(ui_kit, "state_set", lambda k, v: state.__setitem__(k, v))
    monkeypatch.setattr(cs, "ROOT", str(tmp_path))
    alerts = []
    monkeypatch.setattr(cs, "alert_owners", alerts.append)
    return alerts


def test_add_validate_and_limit(store):
    ok, msg, n = cs.add("instagram", _cookie(value="a"))
    assert ok and n == 1
    assert "same login as Cookie-1, not added" in cs.add("instagram", _cookie(value="a"))[1]
    assert "No login cookie" in cs.add("instagram", _cookie(name="csrftoken"))[1]
    assert "expired" in cs.add("instagram", _cookie(value="old", exp=int(time.time()) - 10))[1]
    assert "has no Instagram cookies" in cs.add("instagram", "hello")[1]
    for i in range(cs.MAX_SLOTS - 1):                         # 50 per platform
        assert cs.add("instagram", _cookie(value=f"x{i}"))[0]
    assert f"Max {cs.MAX_SLOTS}" in cs.add("instagram", _cookie(value="one-too-many"))[1]
    assert len(cs.active_paths("instagram")) == cs.MAX_SLOTS


def test_rotation_order_toggle_and_failures(store):
    for v in ("a", "b", "c"):
        cs.add("instagram", _cookie(value=v))
    first, second = cs.active_paths("instagram", rotate=False)[:2]
    sid1 = cs.slots("instagram")[0]["id"]
    cs.toggle("instagram", sid1)
    assert cs.active_paths("instagram", rotate=False)[0] == second
    cs.toggle("instagram", sid1)
    for _ in range(cs.FAIL_ALERT_AT):
        cs.record(first, False, "login required")
    assert first not in cs.active_paths("instagram", rotate=False) and len(store) == 1 and "stopped working" in store[0]
    cs.record(first, True)
    assert first in cs.active_paths("instagram", rotate=False) and "working again" in store[1]


def test_expiry_read_from_file_and_legacy_migration(store, tmp_path):
    exp = int(time.time()) + 5 * 86400
    (tmp_path / "instagram.txt").write_text(_cookie(value="legacy", exp=exp))
    sl = cs.slots("instagram")
    assert len(sl) == 1 and sl[0]["exp"] == exp and "4d left" in cs.exp_text(exp)
    assert not (tmp_path / "instagram.txt").exists()


def test_with_cookiefile_sets_last_opts_dict():
    from COMMANDS.cookies_cmd import _with_cookiefile
    assert _with_cookiefile(("u", {"format": "b"}), "/c.txt") == ("u", {"format": "b", "cookiefile": "/c.txt"})
    assert _with_cookiefile(("u", 3), "/c.txt") == ("u", 3)


def test_only_site_drops_other_sites():
    from COMMANDS import cookie_store as cs
    raw = ("# Netscape HTTP Cookie File\n"
           ".youtube.com\tTRUE\t/\tTRUE\t1900000000\tSAPISID\tx\n"
           ".claude.ai\tTRUE\t/\tTRUE\t1900000000\tsessionKey\tsecret\n"
           ".github.com\tTRUE\t/\tTRUE\t1900000000\t_octo\ty\n"
           ".www.1024terabox.com\tTRUE\t/\tTRUE\t1900000000\tndus\tz\n")
    yt = cs.only_site("youtube", raw)
    assert "SAPISID" in yt and "sessionKey" not in yt and "_octo" not in yt and "ndus" not in yt
    tb = cs.only_site("terabox", raw)
    assert "ndus" in tb and "SAPISID" not in tb


def test_snapchat_and_pinterest_cookies(store):
    pin = ("# Netscape HTTP Cookie File\n.pinterest.com\tTRUE\t/\tTRUE\t4102444800\t_pinterest_sess\tabc\n"
           ".google.com\tTRUE\t/\tTRUE\t4102444800\tSID\tx\n")
    ok, msg = cs.add("pinterest", pin)[:2]
    assert ok, msg
    snap = "# Netscape HTTP Cookie File\n.snapchat.com\tTRUE\t/\tTRUE\t4102444800\t__Host-sc-a-session\tq\n"
    ok, msg = cs.add("snapchat", snap)[:2]
    assert ok, msg
    ok, msg = cs.add("snapchat", snap)[:2]
    assert not ok and "Cookie-1" in msg                   # duplicate still detected without a known login cookie
    assert cs.add("snapchat", "# Netscape HTTP Cookie File\n.google.com\tTRUE\t/\tTRUE\t4102444800\tSID\tx\n")[0] is False


def test_running_now_counts_only_active(monkeypatch):
    from HELPERS import download_status as ds
    from COMMANDS import admin_panel as ap
    monkeypatch.setattr(ds, "active_downloads", {1: False, 2: True, 3: False})
    assert ap._running_downloads() == 1


def test_downloads_take_turns_over_working_cookies(store):
    for v in ("a", "b", "c"):
        cs.add("instagram", _cookie(value=v))
    cs._turn.clear()
    p = cs.active_paths("instagram", rotate=False)
    starts = [cs.active_paths("instagram")[0] for _ in range(4)]
    assert starts == [p[0], p[1], p[2], p[0]]                 # each download starts at the next cookie
    assert sorted(cs.active_paths("instagram")) == sorted(p)    # and can still fall through all of them


def test_remove_dead_keeps_working_and_off(store):
    for v in ("a", "b", "c", "d"):
        cs.add("instagram", _cookie(value=v))
    sl = cs.slots("instagram")
    for _ in range(cs.FAIL_ALERT_AT):
        cs.record(cs._path("instagram", sl[1]["id"]), False)   # b stops working
    cs.toggle("instagram", sl[2]["id"])                       # c is OFF
    assert cs.remove_dead("instagram") == 1
    assert [s["id"] for s in cs.slots("instagram")] == [sl[0]["id"], sl[2]["id"], sl[3]["id"]]


def test_cookie_list_with_35_cookies_fits_telegram(store, monkeypatch):
    from types import SimpleNamespace as NS
    from HELPERS import app_instance
    app_instance.set_app(NS(on_message=lambda *a, **k: (lambda f: f), on_callback_query=lambda *a, **k: (lambda f: f)))
    from COMMANDS import admin_panel as ap
    for i in range(35):
        cs.add("youtube", "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t4102444800\tSID\tv%d\n" % i)
    assert len(cs.slots("youtube")) == 35
    for page in range(4):
        text, kb = ap._cks_view("youtube", page)
        assert len(text) < 4096 and sum(len(r) for r in kb.inline_keyboard) <= 100
    assert "Cookie-31" in ap._cks_view("youtube", 3)[0] and "4/4" in str(ap._cks_view("youtube", 3)[1])


def test_cookie_manager_platforms_two_per_row(store):
    from types import SimpleNamespace as NS
    from HELPERS import app_instance
    app_instance.set_app(NS(on_message=lambda *a, **k: (lambda f: f), on_callback_query=lambda *a, **k: (lambda f: f)))
    from COMMANDS import admin_panel as ap
    rows = ap._ckm_view()[1].inline_keyboard
    platform_rows = [r for r in rows if any((b.callback_data or "").startswith("ap|cks|") for b in r)]
    assert [len(r) for r in platform_rows] == [2, 2, 2]          # 6 platforms in 3 rows of two


def test_terabox_live_check(monkeypatch):
    import requests
    from types import SimpleNamespace as NS
    state = {"errno": -6, "code": 302}

    def get(url, **k):
        if "getinfo" in url:
            return NS(json=lambda: {"errno": state["errno"]})
        return NS(status_code=state["code"])
    monkeypatch.setattr(requests, "get", get)
    assert cs._terabox_live({"ndus": ("x", 0)})[0] is False                  # both say logged out
    state.update(errno=0, code=200)
    assert cs._terabox_live({"ndus": ("x", 0)}) == (True, "working")
    state.update(errno=-6, code=200)
    assert cs._terabox_live({"ndus": ("x", 0)})[0] is True                   # one signal only: keep the cookie


def test_cookie_manager_uses_platform_logos_and_no_dm_text(store):
    from types import SimpleNamespace as NS
    from HELPERS import app_instance
    app_instance.set_app(NS(on_message=lambda *a, **k: (lambda f: f), on_callback_query=lambda *a, **k: (lambda f: f)))
    from COMMANDS import admin_panel as ap
    text, kb = ap._ckm_view()
    assert "DM" not in text and "Error Log group" in text
    ids = {b.text.split(" ·")[0]: getattr(b, "icon_custom_emoji_id", None) for r in kb.inline_keyboard for b in r}
    logos = [ids.get(name) for name in ("Instagram", "Pinterest", "Facebook", "Snapchat") if ids.get(name)]
    assert len(set(logos)) == len(logos)                                     # each platform its own logo


def test_passed_test_sets_last_worked(store, monkeypatch):
    cs.add("instagram", _cookie(value="a"))
    assert not cs.slots("instagram")[0].get("last_ok")
    monkeypatch.setattr(cs, "test_slot", lambda svc, sid: (True, "working"))
    cs.check_all(alert=False, services=["instagram"])
    assert cs.slots("instagram")[0].get("last_ok")                 # no more "ok never" for a working cookie
