from DOWN_AND_UP.ffmpeg import split_video_2

GB = 1024 ** 3


def test_lazy_split_plans_parts_without_cutting(tmp_path):
    r = split_video_2(str(tmp_path), "T", "missing.mp4", int(2.3 * GB), int(1.95 * GB), 18000, 1, lazy=True)
    assert len(r["path"]) == 2 and not any((tmp_path / p).exists() for p in r["path"])
    assert r["ranges"][0] == (0, 9000) and r["ranges"][-1][1] == 18000      # covers the whole video
    assert r["path"][0].endswith("T - Part 1.mp4") and r["path"][1].endswith("T - Part 2.mp4")


def test_split_uses_as_few_parts_as_fit(tmp_path):
    plan = lambda gb: len(split_video_2(str(tmp_path), "T", "x.mp4", int(gb * GB), int(1.95 * GB), 3600, 1, lazy=True)["path"])
    assert plan(2.1) == 2 and plan(3.6) == 2 and plan(3.85) == 2   # Telegram: 2 GB per file
    assert plan(4.0) == 3
