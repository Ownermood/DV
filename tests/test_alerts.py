def test_failures_grouped(monkeypatch):
    from HELPERS import alerts
    sent = []
    monkeypatch.setattr(alerts, "_owners_dm", sent.append)
    alerts._fails.clear()
    for _ in range(3):
        alerts.download_failed(1, "https://www.instagram.com/p/x/", "ERROR: login required")
    assert len(sent) == 1 and "instagram.com" in sent[0] and "login needed" in sent[0]
    alerts.download_failed(1, "https://youtu.be/x", "Sign in to confirm you're not a bot")
    assert len(sent) == 2 and "YouTube bot check" in sent[1]


def test_health_snapshot_reports_failures(monkeypatch):
    from HELPERS import alerts
    monkeypatch.setattr(alerts, "CHECKS", {"good": lambda: True, "bad": lambda: 1 / 0})
    assert alerts.health_snapshot() == {"good": True, "bad": False}


def test_placeholder_proxy_is_no_proxy(monkeypatch):
    from COMMANDS import proxy_cmd
    from HELPERS import proxy_helper
    monkeypatch.setattr(proxy_cmd.Config, "PROXY_IP", "X.X.X.X", raising=False)
    monkeypatch.setattr(proxy_cmd.Config, "PROXY_2_IP", "", raising=False)
    assert proxy_cmd.get_proxy_config() is None and proxy_helper.get_all_proxy_configs() == []
    monkeypatch.setattr(proxy_cmd.Config, "PROXY_IP", "1.2.3.4", raising=False)
    assert proxy_cmd.get_proxy_config()["ip"] == "1.2.3.4"


def test_log_card():
    import types
    from HELPERS.logger import log_card
    user = types.SimpleNamespace(id=42, first_name="Owner", last_name="<Mood>", username="ownermood")
    msg = types.SimpleNamespace(chat=user, from_user=user, text="https://youtu.be/dQw4w9WgXcQ")
    card = log_card(msg, "📥", "Download log", "<b>Video sent</b> 720p")
    assert '<a href="tg://user?id=42">Owner &lt;Mood&gt;</a>' in card and "@ownermood" in card and "<code>42</code>" in card
    assert "YouTube" in card and 'href="https://youtu.be/dQw4w9WgXcQ"' in card and "Video sent" in card and "IST" in card


def test_log_kinds():
    from HELPERS.logger import _log_kind
    assert _log_kind("User entered a <b>url</b>\n <b>user's name:</b> X\nURL: https://youtu.be/x") == ("📥", "New download", "")
    assert _log_kind("Playlist videos sent: 3/3 files (quality=720p) to user 1")[1] == "Sent"
    assert _log_kind("✅ Cookies file is working") is None


def test_short_error_from_old_template():
    from HELPERS.logger import _short_error
    old = ("Supported: YouTube, Instagram\nYou may need cookie for downloading this video. First, clean your workspace via /clean command\n"
           "For Youtube - get cookie via /cookie command.\n────────────────\n❌ Error Code: UNKNOWN_ERROR\n"
           "📝 Description: ERROR: [youtube:truncated_id] jrbRbXX8R9: Incomplete YouTube ID jrbRbXX8R9. URL x looks truncated.\n"
           "🔧 Full Error: ERROR: [youtube:truncated_id] ...")
    out = _short_error(old)
    assert "/clean" not in out and "cookie" not in out.lower() and "Full Error" not in out
    assert "incomplete or broken" in out and "truncated_id" in out


def test_user_side_errors_do_not_alert(monkeypatch):
    from HELPERS import alerts
    sent = []
    monkeypatch.setattr(alerts, "_owners_dm", sent.append)
    alerts._fails.clear()
    old = ("<blockquote>Check <a href='x'>here</a> if your site supported</blockquote>\n❌ Error Code: UNKNOWN_ERROR\n"
           "📝 Description: ERROR: [youtube:truncated_id] jrbRbXX8R9: Incomplete YouTube ID jrbRbXX8R9. URL x looks truncated.")
    alerts.download_failed(1, "https://www.youtube.com/watch?v=jrbRbXX8R9", old)
    alerts.download_failed(1, "https://youtu.be/x", "ERROR: [youtube] x: Private video. Sign in if you've been granted access")
    assert sent == []
    alerts.download_failed(1, "https://youtu.be/x", "<b>❌ Error Code: X</b>\n📝 Description: ERROR: Sign in to confirm you're not a bot")
    assert len(sent) == 1 and "<blockquote>Check" not in sent[0] and "not a bot" in sent[0]


def test_alerts_go_to_the_error_log_group(monkeypatch):
    from types import SimpleNamespace as NS
    from COMMANDS import cookie_store
    from HELPERS import app_instance
    from CONFIG.config import Config
    sent = []
    monkeypatch.setattr(app_instance, "get_app", lambda: NS(send_message=lambda chat, text, **k: sent.append(chat)))
    monkeypatch.setattr(Config, "LOG_EXCEPTION", -1004373672153, raising=False)
    cookie_store.alert_owners("x")
    assert sent == [-1004373672153]          # the group, not the owners' DMs
