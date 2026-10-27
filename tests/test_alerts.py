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
