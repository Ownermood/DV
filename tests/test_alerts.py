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
