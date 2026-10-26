import os, pathlib
import pytest

os.environ.setdefault("BOT_TOKEN", "1:test"); os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "x"); os.environ.setdefault("ADMIN_IDS", "1")
os.environ["DASHBOARD_PASSWORD"] = "correct-horse-battery-staple-1"
os.environ["REQUIRE_VALID_CONFIG"] = "false"

from fastapi.testclient import TestClient
from web.dashboard_app import app
from services import system_service

CFG = pathlib.Path(__file__).resolve().parent.parent / "CONFIG" / "config.py"

def login(c):
    return c.post("/api/login", json={"username": "admin", "password": os.environ["DASHBOARD_PASSWORD"]})

def test_reset_lockdown_requires_auth():
    assert TestClient(app).post("/api/reset-lockdown").status_code == 401

def test_login_ok_and_cookie_flags():
    c = TestClient(app, base_url="https://testserver")
    r = login(c)
    assert r.status_code == 200
    sc = r.headers["set-cookie"].lower()
    assert "httponly" in sc and "secure" in sc

def test_csrf_origin_blocked():
    c = TestClient(app)
    assert login(c).status_code == 200
    assert c.post("/api/restart-service", headers={"Origin": "https://evil.example"}).status_code == 403

def test_no_wildcard_cors():
    r = TestClient(app).options("/api/login", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert r.headers.get("access-control-allow-origin") != "*"

@pytest.mark.parametrize("payload", ['x"\n__import__("os").system("id")\n#', 'a\\', 'q\r\nimport os'])
def test_config_injection_blocked(payload):
    bak = CFG.read_bytes()
    try:
        try:
            system_service.update_config_setting("PROXY_IP", payload)
        except Exception:
            pass
        text = CFG.read_text(encoding="utf-8")
        compile(text, "config.py", "exec")
        assert "__import__" not in text
    finally:
        CFG.write_bytes(bak)

def test_bad_key_and_int_rejected():
    bak = CFG.read_bytes()
    try:
        for k, v in (("PROXY_IP\nEVIL", "1"), ("LOGS_ID", "1; import os")):
            try:
                system_service.update_config_setting(k, v)
            except Exception:
                pass
            text = CFG.read_text(encoding="utf-8")
            compile(text, "config.py", "exec")
            assert "EVIL" not in text
    finally:
        CFG.write_bytes(bak)

def test_no_password_disables_login(monkeypatch):
    from services import auth_service
    monkeypatch.setattr(auth_service.Config, "DASHBOARD_PASSWORD", "")
    svc = auth_service.AuthService()
    with pytest.raises(ValueError):
        svc.login("admin", "admin123", "1.2.3.4")
