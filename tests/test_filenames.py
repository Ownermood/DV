import os

import yt_dlp

from HELPERS.filesystem_hlp import sanitize_filename, sanitize_filename_strict

LONG = "मेरा वीडियो 🔥🔥 देखो दोस्तों " * 20 + ".mp4"      # a Facebook "title" = the whole post


def test_long_titles_still_make_a_file(tmp_path):
    for fn in (sanitize_filename, sanitize_filename_strict):
        name = fn(LONG)
        assert len(name.encode()) <= 200 and name.endswith(".mp4")
        path = tmp_path / name
        path.write_bytes(b"x")                                         # was: OSError File name too long
        assert path.exists()


def test_video_template_caps_the_title(tmp_path):
    y = yt_dlp.YoutubeDL({"outtmpl": os.path.join(str(tmp_path), "%(title).100B.%(ext)s")})
    name = y.prepare_filename({"title": LONG, "ext": "mp4", "id": "x"})
    assert len(os.path.basename(name).encode()) <= 110
    open(name, "w").close()                                            # aria2c exit 16 came from this
