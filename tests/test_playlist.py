import time
from types import SimpleNamespace as NS

from HELPERS import app_instance


class _FakeApp:
    def __getattr__(self, name):
        return lambda *a, **k: (lambda f: f)


app_instance.set_app(_FakeApp())
from COMMANDS import playlist as pl, simple_ui as ui  # noqa: E402


def test_playlist_links():
    assert pl.is_playlist_url("https://www.youtube.com/playlist?list=PLabc")
    assert pl.is_playlist_url("https://m.youtube.com/playlist/?list=PLabc&si=x")
    assert not pl.is_playlist_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLabc")   # one video
    assert not pl.is_playlist_url("https://www.youtube.com/playlist")


def _job(n, total=None):
    return {"token": "tk", "msg": NS(id=1, chat=NS(id=7), from_user=NS(id=7)), "title": "Mix", "uploader": "Music",
            "total": total or n, "urls": [f"https://youtu.be/v{i}" for i in range(n)], "count": n, "q": "b", "ts": 0}


def test_card_counts_quality_and_start():
    text, kb = pl._card(_job(50, total=182))
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert "182" in text and "Up to 50" in text
    assert any("First 10" in l for l in labels) and any("First 25" in l for l in labels) and any("All 50" in l for l in labels)
    assert any("MP3" in l for l in labels) and any("Start · 50 videos" in l for l in labels)
    labels = [b.text for row in pl._card(_job(6))[1].inline_keyboard for b in row]
    assert any("All 6" in l for l in labels) and not any("First 10" in l for l in labels)   # small list: just All


def test_videos_run_one_by_one_and_stop(monkeypatch):
    ran = []
    monkeypatch.setattr(pl, "edit", lambda *a, **k: None)

    def fake_choice(client, m, q):
        ran.append((m.text, q))
        if len(ran) == 3:
            pl.stop(7)          # Cancel / Stop during video 3

    monkeypatch.setattr(ui, "_run_choice", fake_choice)
    job = _job(10)
    job["count"], job["q"] = 5, "720"
    pl._run(None, job, NS())
    assert ran == [(f"https://youtu.be/v{i}", "720") for i in range(3)]   # stopped after video 3
    assert 7 not in pl._STOP                                              # next playlist starts clean
