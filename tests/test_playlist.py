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


def _job(n, total=None, sel=None):
    return {"token": "tk", "msg": NS(id=1, chat=NS(id=7), from_user=NS(id=7)), "title": "Mix", "uploader": "Music",
            "total": total or n, "urls": [f"https://youtu.be/v{i}" for i in range(n)], "sel": sel or [], "page": 0,
            "qs": ["b"], "ts": 0}


def test_parts_of_ten_and_pages():
    labels = [b.text for r in pl._card(_job(100))[1].inline_keyboard for b in r]
    assert labels[:4] == ["1–10", "11–20", "21–30", "31–40"] and "91–100" in labels and "Next" not in labels
    job = _job(150)                                                       # 15 parts: 12 on page 1, 3 on page 2
    labels = [b.text for r in pl._card(job)[1].inline_keyboard for b in r]
    assert "111–120" in labels and "121–130" not in labels and "1/2" in labels
    job["page"] = 1
    labels = [b.text for r in pl._card(job)[1].inline_keyboard for b in r]
    assert labels[:3] == ["121–130", "131–140", "141–150"] and "2/2" in labels
    assert pl.chosen_urls(_job(100, sel=[1])) == [f"https://youtu.be/v{i}" for i in range(10, 20)]   # videos 11-20
    assert [b.text for b in pl._card(_job(13))[1].inline_keyboard[0]] == ["1–10", "11–13"]


def test_card_colours_only_on_start_and_close():
    from pyrogram import enums
    rows = pl._card(_job(13))[1].inline_keyboard                         # as the card opens: only the quality is chosen
    assert [len(r) for r in rows] == [2, 3, 3, 2]
    start, close = rows[-1]
    assert start.style == enums.ButtonStyle.SUCCESS and close.style == enums.ButtonStyle.DANGER
    chosen = [b for r in rows[:-1] for b in r if b.style == enums.ButtonStyle.SUCCESS]
    assert [b.text for b in chosen] == ["Best"]
    assert all(b.style in (enums.ButtonStyle.DEFAULT, enums.ButtonStyle.SUCCESS) for r in rows[:-1] for b in r)


def test_videos_run_one_by_one_and_stop(monkeypatch):
    ran = []
    monkeypatch.setattr(pl, "edit", lambda *a, **k: None)

    def fake_choice(client, m, q):
        ran.append((m.text, q))
        if len(ran) == 3:
            pl.stop(7)          # Cancel / Stop during video 3

    monkeypatch.setattr(ui, "_run_choice", fake_choice)
    job = _job(30, sel=[1])
    job["qs"] = ["720"]
    pl._run(None, job, NS())
    assert ran == [(f"https://youtu.be/v{i}", "720") for i in range(10, 13)]   # part 11–20, stopped after 3
    assert 7 not in pl._STOP


def test_several_qualities_each_video_in_each(monkeypatch):
    ran = []
    monkeypatch.setattr(pl, "edit", lambda *a, **k: None)
    monkeypatch.setattr(ui, "_run_choice", lambda c, m, q: ran.append((m.text, q)))
    job = _job(2, sel=[0])
    job["qs"] = ["720", "a"]
    assert "× 2" in str(pl._card(job)[1])
    pl._run(None, job, NS())
    assert ran == [("https://youtu.be/v0", "720"), ("https://youtu.be/v0", "a"),
                   ("https://youtu.be/v1", "720"), ("https://youtu.be/v1", "a")]


def test_tap_selects_tap_again_unselects(monkeypatch):
    monkeypatch.setattr(pl, "edit", lambda *a, **k: None)
    monkeypatch.setattr(pl, "max_count", lambda: 50)
    job = _job(100)
    pl._JOBS["tk"] = job
    answers = []

    def tap(data):
        cq = NS(data=data, from_user=NS(id=7), message=NS(delete=lambda: None),
                answer=lambda *a, **k: answers.append(a[0] if a else ""), stop_propagation=lambda: None)
        pl.playlist_buttons(None, cq)
    tap("pl|tk|go")
    assert "videos first" in answers[-1]                                 # nothing chosen: Start explains
    tap("pl|tk|b|1"); assert job["sel"] == [1]                           # 11–20
    tap("pl|tk|b|1"); assert job["sel"] == []                            # tap again: plain again
    for k in range(5):
        tap(f"pl|tk|b|{k}")
    tap("pl|tk|b|5")
    assert len(job["sel"]) == 5 and "Max 50" in answers[-1]              # 60 > 50: refused
    tap("pl|tk|q|b")
    tap("pl|tk|go")
    assert "quality" in answers[-1] and "tk" in pl._JOBS
