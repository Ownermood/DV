from HELPERS import app_instance


class _FakeApp:
    def __getattr__(self, name):
        return lambda *a, **k: (lambda f: f)


app_instance.set_app(_FakeApp())
from COMMANDS import ig_post  # noqa: E402


def test_carousel_items_photos_and_videos():
    info = {"_type": "playlist", "entries": [
        {"formats": [], "thumbnails": [{"url": "small", "width": 150, "height": 150},
                                       {"url": "full", "width": 1290, "height": 2293}]},          # photo
        {"formats": [{"url": "v720", "vcodec": "h264", "acodec": "aac", "height": 720},
                     {"url": "v1080", "vcodec": "h264", "acodec": "aac", "height": 1080}]},        # video with sound
        {"formats": [{"url": "vonly", "vcodec": "vp9", "acodec": "none"}], "webpage_url": "https://ig/p/x"},  # split video
        None,
        {"formats": [], "thumbnails": [{"url": "a"}, {"url": "b"}]},                               # photo, no sizes: last
    ]}
    assert ig_post._items(info) == [("photo", "full"), ("video", "v1080"), ("video_merge", "https://ig/p/x"),
                                    ("photo", "b")]


def test_single_photo_post():
    assert ig_post._items({"formats": [], "thumbnails": [{"url": "only"}]}) == [("photo", "only")]
