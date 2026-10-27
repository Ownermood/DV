import threading
import time

from HELPERS import cancel
from HELPERS import progress_ui as ui


def test_flag_and_check():
    cancel.clear(5)
    cancel.check(5)
    cancel.request(5)
    try:
        cancel.check(5)
        assert False, "should raise"
    except cancel.Cancelled:
        pass
    cancel.clear(5)
    assert not cancel.is_set(5)


def test_progress_cards_get_cancel_button():
    calls = []

    def fake(chat, mid, text, **kw):
        calls.append(kw)
        return True
    ui.edit_card(fake, 7, 1, ui.progress_card("download", 40))
    ui.edit_card(fake, 7, 1, ui.progress_card("upload", 40))
    ui.edit_card(fake, 7, 1, ui.progress_card("search", 0))
    btns = [kw.get("reply_markup") for kw in calls]
    assert btns[0] and btns[1] and btns[2] is None
    assert btns[0].inline_keyboard[0][0].callback_data == "cx|7"


def test_fast_download_stops_on_cancel(tmp_path, monkeypatch):
    import http.server
    import socketserver
    from ADAPTERS import terabox as tb
    data = b"x" * (20 * 1024 * 1024)

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            import re
            m = re.match(r"bytes=(\d+)-(\d+)", self.headers.get("Range", ""))
            a, b = (int(m[1]), int(m[2])) if m else (0, len(data) - 1)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {a}-{b}/{len(data)}")
            self.send_header("Content-Length", str(b - a + 1))
            self.end_headers()
            for i in range(a, b + 1, 65536):  # slow server
                self.wfile.write(data[i:min(i + 65536, b + 1)])
                time.sleep(0.05)

        def log_message(self, *a):
            pass

    class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
    srv = S(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    cancel.clear(9)
    threading.Timer(0.5, cancel.request, args=(9,)).start()
    t0 = time.time()
    try:
        tb._fast_download(f"http://127.0.0.1:{srv.server_address[1]}/f", tmp_path / "f", None, len(data),
                          4 * 1024 ** 3, lambda done, total: cancel.check(9))
        assert False, "should be cancelled"
    except cancel.Cancelled:
        pass
    assert time.time() - t0 < 10
    cancel.clear(9)
    srv.shutdown()
