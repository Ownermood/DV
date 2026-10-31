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
    assert [len(r) for r in rows] == [2, 1, 3, 3, 2]                  # parts / All / qualities / qualities / Start+Close
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


def test_done_card_offers_the_next_part(monkeypatch):
    sent = []
    monkeypatch.setattr(pl, "send", lambda c, chat, build, **k: sent.append(build()) or NS(delete=lambda: None))
    monkeypatch.setattr(ui, "_run_choice", lambda c, m, q: None)
    job = _job(100, sel=[0])
    pl._run(None, job, NS(delete=lambda: None))
    text, kb = sent[-1]
    labels = [b.text for r in kb.inline_keyboard for b in r]
    assert "Playlist part done" in text and "Next: 11–20 of 100" in text
    assert "Next 11–20" in labels and "Choose Parts" in labels           # one tap for the next 10
    nxt = pl._JOBS[kb.inline_keyboard[0][0].callback_data.split("|")[1]]
    assert nxt["sel"] == [1] and nxt["qs"] == job["qs"]
    sent.clear()
    pl._run(None, _job(10, sel=[0]), NS(delete=lambda: None))
    assert sent[-1][1] is None                                             # last part: no Next button


def test_playlist_videos_skip_the_per_minute_limit(monkeypatch):
    marks = []
    monkeypatch.setattr(pl, "send", lambda *a, **k: NS(delete=lambda: None))
    monkeypatch.setattr(ui, "_run_choice", lambda c, m, q: marks.append(getattr(m, "_playlist", False)))
    pl._run(None, _job(12, sel=[0, 1]), NS(delete=lambda: None))
    assert marks == [True] * 12                                            # url_distractor skips check_rate_limit


def test_all_button(monkeypatch):
    monkeypatch.setattr(pl, "edit", lambda *a, **k: None)
    monkeypatch.setattr(pl, "max_count", lambda: 50)
    labels = [b.text for r in pl._card(_job(13))[1].inline_keyboard for b in r]
    assert "All 13" in labels
    assert "First 50" in [b.text for r in pl._card(_job(182))[1].inline_keyboard for b in r]   # big list: one run max
    job = _job(13)
    pl._JOBS["tk"] = job
    tap = lambda d: pl.playlist_buttons(None, NS(data=d, from_user=NS(id=7), message=NS(delete=lambda: None),
                                                 answer=lambda *a, **k: None, stop_propagation=lambda: None))
    tap("pl|tk|all")
    assert job["sel"] == [0, 1] and len(pl.chosen_urls(job)) == 13          # all 13 chosen, green
    tap("pl|tk|all")
    assert job["sel"] == []                                                # tap again: none
    big = _job(182)
    pl._JOBS["tk"] = big
    tap("pl|tk|all")
    assert len(pl.chosen_urls(big)) == 50                                  # parts 1–50


def test_parts_three_per_row_and_red_close():
    from pyrogram import enums
    rows = pl._card(_job(43))[1].inline_keyboard
    part_rows = [r for r in rows if all("–" in b.text for b in r)]
    assert [len(r) for r in part_rows] == [3, 2]                          # 1–10 11–20 21–30 / 31–40 41–43
    assert rows[-1][1].text == "Close" and rows[-1][1].style == enums.ButtonStyle.DANGER


def test_every_close_button_is_red():
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    for f in ("COMMANDS/simple_ui.py", "COMMANDS/playlist.py", "COMMANDS/admin_panel.py", "ADAPTERS/terabox.py"):
        for line in (root / f).read_text().splitlines():
            if re.search(r'btn\("(trash|exit)", (t\(uid, "close"\)|"Close")', line):
                assert "RED" in line or "DANGER" in line, f"{f}: {line.strip()}"


def test_unused_group_playlist_card_goes(monkeypatch):
    deleted = []
    monkeypatch.setattr(ui, "GROUP_MENU_TTL", 0.05)
    monkeypatch.setattr(pl, "_read", lambda url: {"title": "Mix", "uploader": "", "total": 3, "urls": ["a", "b", "c"]})
    monkeypatch.setattr(pl, "send", lambda *a, **k: NS(id=50))
    monkeypatch.setattr(pl, "edit", lambda *a, **k: None)
    client = NS(delete_messages=lambda chat, ids: deleted.append((chat, ids)))
    msg = NS(id=9, chat=NS(id=-100), from_user=NS(id=7), message_thread_id=None, is_topic_message=False)
    pl.show(client, msg, "https://www.youtube.com/playlist?list=PLx")
    time.sleep(0.3)
    assert deleted == [(-100, [50, 9])]                               # card + link message, like other group menus


def test_close_only_by_the_asker_in_groups(monkeypatch):
    answers, deleted = [], []
    monkeypatch.setattr(ui, "_is_admin", lambda uid: False)

    def tap(who):
        msg = NS(chat=NS(id=-100), reply_to_message=NS(from_user=NS(id=7)), delete=lambda: deleted.append(who))
        cq = NS(data="ui|close", from_user=NS(id=who, first_name="x"), message=msg,
                answer=lambda *a, **k: answers.append(a[0] if a else ""), stop_propagation=lambda: None)
        ui.ui_callbacks(None, cq)
    tap(8)
    assert deleted == [] and "Only the person" in answers[0]            # someone else: refused
    tap(7)
    assert deleted == [7]                                              # the asker: closed
