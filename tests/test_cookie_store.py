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
    assert "already added as Cookie-1" in cs.add("instagram", _cookie(value="a"))[1]
    assert "No login cookie" in cs.add("instagram", _cookie(name="csrftoken"))[1]
    assert "expired" in cs.add("instagram", _cookie(value="old", exp=int(time.time()) - 10))[1]
    assert "Netscape" in cs.add("instagram", "hello")[1]
    for i in range(9):
        assert cs.add("instagram", _cookie(value=f"x{i}"))[0]
    assert "Max 10" in cs.add("instagram", _cookie(value="eleven"))[1]
    assert len(cs.active_paths("instagram")) == 10


def test_rotation_order_toggle_and_failures(store):
    for v in ("a", "b", "c"):
        cs.add("instagram", _cookie(value=v))
    first, second = cs.active_paths("instagram")[:2]
    sid1 = cs.slots("instagram")[0]["id"]
    cs.toggle("instagram", sid1)
    assert cs.active_paths("instagram")[0] == second
    cs.toggle("instagram", sid1)
    for _ in range(cs.FAIL_ALERT_AT):
        cs.record(first, False, "login required")
    assert first not in cs.active_paths("instagram") and len(store) == 1 and "stopped working" in store[0]
    cs.record(first, True)
    assert first in cs.active_paths("instagram") and "working again" in store[1]


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
