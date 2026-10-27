def test_pot_base_url_reaches_the_plugin(monkeypatch):
    """yt-dlp gives a PO token provider extractor_args["youtubepot-<key>"]; a nested dict is ignored."""
    from HELPERS import pot_helper as ph
    monkeypatch.setattr(ph, "check_pot_provider_availability", lambda u: True)
    opts = ph.add_pot_to_ytdl_opts({"extractor_args": {"youtubetab": {"skip": ["authcheck"]}}},
                                   "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert opts["extractor_args"]["youtubepot-bgutilhttp"] == {"base_url": [ph.Config.YOUTUBE_POT_BASE_URL]}
    assert opts["extractor_args"]["youtubetab"] == {"skip": ["authcheck"]}
    assert "youtubepot" not in opts["extractor_args"] and not opts.get("verbose")
    assert ph.add_pot_to_ytdl_opts({}, "https://www.instagram.com/p/x/") == {}
