
def get_quality_by_min_side(width: int, height: int) -> str:
    """
    Determines the quality by the smaller side of the video.
    Works for both horizontal and vertical videos.
    For example, for 1280×720 returns '720p', for 720×1280 also '720p'.
    """
    min_side = min(width, height)
    quality_map = {
        144: "144p", 256: "144p",
        240: "240p", 426: "240p",
        360: "360p", 640: "360p",
        480: "480p", 854: "480p",
        540: "540p", 960: "540p",
        576: "576p", 1024: "576p",
        720: "720p", 1280: "720p",
        1080: "1080p", 1920: "1080p",
        1440: "1440p", 2560: "1440p",
        2160: "2160p", 3840: "2160p",
        4320: "4320p", 7680: "4320p"
    }
    return quality_map.get(min_side, "best")



# round height to popular quality for cache only
# --- Round height to nearest higher popular quality ---
def ceil_to_popular(h):
    popular = [144, 240, 360, 480, 540, 576, 720, 1080, 1440, 2160, 4320]
    for p in popular:
        if h <= p:
            return p
    return popular[-1]


