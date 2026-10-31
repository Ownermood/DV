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
            "total": total or n, "urls": [f"https://youtu.be/v{i}" for i in range(n)], "count": n, "qs": ["b"], "ts": 0}


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
    job["count"], job["qs"] = 5, ["720"]
    pl._run(None, job, NS())
    assert ran == [(f"https://youtu.be/v{i}", "720") for i in range(3)]   # stopped after video 3
    assert 7 not in pl._STOP                                              # next playlist starts clean


def test_card_colours_only_on_start_and_close():
    from pyrogram import enums
    job = _job(13)
    job["count"] = 0                                                    # as the card opens: only the quality is chosen
    rows = pl._card(job)[1].inline_keyboard
    assert [len(r) for r in rows] == [2, 3, 3, 2]                     # counts / 3 qualities / 3 qualities / Start+Close
    start, close = rows[-1]
    assert start.style == enums.ButtonStyle.SUCCESS and close.style == enums.ButtonStyle.DANGER
    chosen = [b for r in rows[:-1] for b in r if b.style == enums.ButtonStyle.SUCCESS]
    assert [b.text for b in chosen] == ["Best"]                         # nothing else preselected
    assert all(b.style in (enums.ButtonStyle.DEFAULT, enums.ButtonStyle.SUCCESS) for r in rows[:-1] for b in r)


def test_several_qualities_each_video_in_each(monkeypatch):
    ran = []
    monkeypatch.setattr(pl, "edit", lambda *a, **k: None)
    monkeypatch.setattr(ui, "_run_choice", lambda c, m, q: ran.append((m.text, q)))
    job = _job(2)
    job["qs"] = ["720", "a"]
    assert "× 2" in str(pl._card(job)[1])
    pl._run(None, job, NS())
    assert ran == [("https://youtu.be/v0", "720"), ("https://youtu.be/v0", "a"),
                   ("https://youtu.be/v1", "720"), ("https://youtu.be/v1", "a")]


def test_tap_selects_tap_again_unselects(monkeypatch):
    monkeypatch.setattr(pl, "edit", lambda *a, **k: None)
    job = _job(13)
    job["count"] = 0
    pl._JOBS["tk"] = job
    answers = []

    def tap(data):
        cq = NS(data=data, from_user=NS(id=7), message=NS(delete=lambda: None),
                answer=lambda *a, **k: answers.append(a[0] if a else ""), stop_propagation=lambda: None)
        pl.playlist_buttons(None, cq)
    tap("pl|tk|go")
    assert "how many" in answers[-1]                                    # nothing chosen: Start explains
    tap("pl|tk|c|10"); assert job["count"] == 10
    tap("pl|tk|c|10"); assert job["count"] == 0                         # tap again: plain again
    tap("pl|tk|q|720"); assert job["qs"] == ["b", "720"]
    tap("pl|tk|q|b"); tap("pl|tk|q|720"); assert job["qs"] == []        # every quality can be unchosen
    tap("pl|tk|c|13"); tap("pl|tk|go")
    assert "quality" in answers[-1] and "tk" in pl._JOBS                 # still waiting for a quality
