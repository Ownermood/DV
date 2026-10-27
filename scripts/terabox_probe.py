"""TeraBox speed probe (run inside the app container with a Cookie Manager TeraBox cookie):

    docker compose exec app python scripts/terabox_probe.py https://1024terabox.com/s/1xxxx

1. copies the shared file into the cookie's own account (/share/transfer, folder /dvbot)
2. asks own-file streaming for 1080p / 720p / 480p (errno 130 = still transcoding)
3. gets the own-file download link + locatedownload mirrors and measures 15 s of speed
Prints results only; the copied file stays in /dvbot of that account.
"""
import json
import os
import re
import sys
import time
import urllib.parse

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ADAPTERS import terabox as tb  # noqa: E402

ROOT = "/dvbot"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def speed(sess, url, seconds=15, conns=1):
    import threading
    got, stop = [0], time.time() + seconds

    def one(offset):
        try:
            h = {"Range": f"bytes={offset}-"} if offset else {}
            with sess.get(url, headers=h, stream=True, timeout=(10, 20)) as r:
                for c in r.iter_content(65536):
                    got[0] += len(c)
                    if time.time() > stop:
                        return
        except Exception as e:
            print("   conn error:", str(e)[:80])
    ts = [threading.Thread(target=one, args=(i * 50 * 1024 * 1024,)) for i in range(conns)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    return got[0] / seconds / 1024 ** 2


def main(link):
    paths = tb._cookie_files()
    if not paths:
        sys.exit("No TeraBox cookie in Cookie Manager")
    ck = tb._load_cookies(paths[0])
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    s.cookies.update(ck)
    m = tb._meta(link, ck)
    base = m["base"]
    print("file:", m["title"], round(m["size"] / 1024 ** 2), "MB, logged in:", m["logged_in"])

    page = s.get(f"{base}/main", headers={"Referer": f"{base}/"}, timeout=20).text
    bds = re.search(r'["\']bdstoken["\']?\s*[:=]\s*["\']([^"\']+)', page)
    if not bds:
        sys.exit("No bdstoken: cookie expired?")
    bds = bds.group(1)
    js = m["common"]["jsToken"]
    surl = re.search(r"/s/1?([\w-]+)", urllib.parse.urlparse(link).path).group(1)
    info = s.get(f"{base}/api/shorturlinfo", params={**m["common"], "shorturl": "1" + surl, "root": "1"}, timeout=20).json()
    randsk = urllib.parse.unquote(info.get("randsk", ""))
    if randsk:
        s.cookies.set("TSID", randsk)
    common = {"app_id": "250528", "web": "1", "channel": "dubox", "clienttype": "0", "jsToken": js}
    # the /dvbot folder (fine if it already exists)
    s.post(f"{base}/api/create", params={**common, "bdstoken": bds}, data={"path": ROOT, "isdir": "1", "block_list": "[]"}, timeout=20)
    t0 = time.time()
    tr = s.post(f"{base}/share/transfer", params={**common, "shareid": m["shareid"], "from": m["uk"], "sekey": randsk,
                                                  "ondup": "newcopy", "async": "2", "bdstoken": bds},
                data={"fsidlist": json.dumps([int(m["fid"])]), "path": ROOT},
                headers={"Referer": f"{base}/", "Origin": base}, timeout=30).json()
    print("1) transfer:", tr.get("errno"), tr.get("show_msg", ""), f"({time.time() - t0:.1f}s)")
    if tr.get("errno") not in (0, 4, 12):
        sys.exit("transfer failed")
    time.sleep(3)
    lst = s.get(f"{base}/api/list", params={**common, "dir": ROOT, "num": "100", "order": "time", "desc": "1"}, timeout=20).json()
    mine = next((f for f in lst.get("list") or [] if f.get("server_filename") == m["title"]), None)
    if not mine:
        sys.exit(f"copied file not found in {ROOT}")
    path = mine["path"]
    print("   own path:", path)

    print("2) own-file streaming:")
    for q in ("1080", "720", "480"):
        r = s.get(f"{base}/api/streaming", params={**common, "path": path, "type": f"M3U8_AUTO_{q}", "bdstoken": bds,
                                                   "isplayer": "1", "check_blue": "1", "clienttype": "1",
                                                   "resolution": f"{q}p"}, timeout=20)
        ok = r.text.startswith("#EXTM3U")
        print(f"   {q}p:", "OK" if ok else r.text[:90])

    print("3) own-file download links:")
    metas = s.get(f"{base}/api/filemetas", params={**common, "fsids": json.dumps([mine["fs_id"]]), "dlink": "1",
                                                   "bdstoken": bds}, timeout=20).json()
    dlink = ((metas.get("info") or metas.get("list") or [{}])[0]).get("dlink")
    if dlink:
        print(f"   filemetas dlink, 1 conn: {speed(s, dlink):.2f} MB/s")
        print(f"   filemetas dlink, 8 conn: {speed(s, dlink, conns=8):.2f} MB/s")
    loc = s.post(f"{base}/rest/2.0/pcs/file", params={"ant": "1", "app_id": "250528", "channel": "0", "check_blue": "1",
                                                      "clienttype": "17", "method": "locatedownload", "path": path,
                                                      "vip": "2"}, data=" =", timeout=20)
    try:
        urls = [u.get("url") for u in loc.json().get("urls", []) if u.get("url")]
    except ValueError:
        urls = []
    print("   locatedownload mirrors:", len(urls), "" if urls else loc.text[:90])
    if urls:
        print(f"   mirror, 1 conn: {speed(s, urls[0]):.2f} MB/s")
        print(f"   mirror, 8 conn: {speed(s, urls[0], conns=8):.2f} MB/s")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else input("TeraBox link: ").strip())
