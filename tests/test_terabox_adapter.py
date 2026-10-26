from ADAPTERS.terabox import is_terabox_url, _extract_direct_link


def test_recognizes_supported_terabox_hosts():
    assert is_terabox_url("https://www.terabox.com/s/abc")
    assert is_terabox_url("https://1024terabox.com/sharing/link")


def test_rejects_lookalike_and_non_http_urls():
    assert not is_terabox_url("https://terabox.com.attacker.example/s/abc")
    assert not is_terabox_url("javascript://terabox.com")
    assert not is_terabox_url("https://example.com/terabox.com")


def test_extracts_common_resolution_payload():
    direct, thumb, title = _extract_direct_link({"response": [{"resolutions": {"HD Video": "https://cdn.example/video.mp4"}, "thumb": "https://cdn.example/thumb.jpg", "file_name": "video.mp4"}]})
    assert direct == "https://cdn.example/video.mp4"
    assert thumb == "https://cdn.example/thumb.jpg"
    assert title == "video.mp4"
