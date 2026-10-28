from DOWN_AND_UP.ffmpeg import split_video_2

GB = 1024 ** 3


def test_lazy_split_plans_parts_without_cutting(tmp_path):
    r = split_video_2(str(tmp_path), "T", "missing.mp4", int(2.3 * GB), int(1.95 * GB), 18000, 1, lazy=True)
    assert len(r["path"]) == 2 and not any((tmp_path / p).exists() for p in r["path"])
    assert r["ranges"][0] == (0, 9000) and r["ranges"][-1][1] == 18000      # covers the whole video
    assert r["path"][0].endswith("T - Part 1.mp4") and r["path"][1].endswith("T - Part 2.mp4")


def test_split_keeps_headroom_for_uneven_bitrate(tmp_path):
    r = split_video_2(str(tmp_path), "T", "x.mp4", int(3.8 * GB), int(1.95 * GB), 3600, 1, lazy=True)
    assert len(r["path"]) == 3   # 3.8 / 2 parts = 1.9 GB each would be too close to the limit
