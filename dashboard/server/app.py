"""HTTP server: static UI, /api/state, demo controls and evidence clips.

Python standard library only, so it runs offline with nothing to install.
"""
import json
import os
import re
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import timeutil as T
from .adapters import build
from .snapshot import Snapshotter

ROOT = Path(__file__).resolve().parent.parent
_feed_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # local streams only, no env proxies
WEB = ROOT / "web"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".json": "application/json", ".svg": "image/svg+xml",
         ".mp4": "video/mp4", ".webm": "video/webm", ".png": "image/png", ".ico": "image/x-icon"}
PATH_KEYS = {("calendar", "sqlite_path"), ("cell", "clips_root"), ("messages", "jsonl_path"),
             ("system", "egress_counter_file")}


def load_config(path, mode=None, port=None):
    cfg = json.loads(Path(path).read_text(encoding="utf-8-sig"))  # tolerate a BOM from Windows editors
    if mode:
        cfg["mode"] = mode
    if port:
        cfg["port"] = port
    for (src, key) in PATH_KEYS:  # relative paths are relative to the dashboard folder
        val = cfg.get("live", {}).get(src, {}).get(key)
        if val and not os.path.isabs(val):
            cfg["live"][src][key] = str((ROOT / val).resolve())
    return cfg


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        T.configure(cfg["demo"]["tz"], cfg["demo"].get("tz_offset", "-04:00"))
        self.adapters, self.modes = build(cfg, ROOT)
        self.snap = Snapshotter(self.adapters, self.modes, cfg)

    # Controls call each service's own endpoint through its adapter.
    def control(self, action, body):
        a = self.adapters
        if action == "clock":
            t = body.get("time") or None  # "13:25" is passed to a live clock as-is
            sim = T.parse(t, self.cfg["demo"]["date"]) if t and self.modes["clock"] == "mock" else t
            a["clock"].set(sim, body.get("speed"))
        elif action == "delay":
            a["calendar"].inject_delay(body.get("line", "Red"), int(body.get("minutes", 12)))
        elif action == "fire":
            a["tickets"].fire_need(body.get("need_id", "T-12"))
        elif action == "reset":
            errors = []
            for name in ("tickets", "calendar", "cell", "clock"):
                try:
                    a[name].reset()
                except NotImplementedError:
                    pass
                except Exception as e:
                    errors.append(f"{name}: {e}")
            if errors:
                raise RuntimeError("; ".join(errors))
        else:
            raise KeyError(action)
        self.snap.refresh()

    # Calendar edits go to Field (its write API), which re-checks travel and notifies people.
    def edit(self, action, body):
        cal = self.adapters["calendar"]
        if action == "event":
            fields = {k: v for k, v in (body.get("set") or {}).items() if k in ("title", "place", "start", "end")}
            cal.update_event(body["event_id"], fields, body.get("reason"))
        elif action == "create":
            fields = {k: v for k, v in (body.get("set") or {}).items() if k in ("title", "type", "place", "start", "end")}
            cal.create_event(body["staff_id"], fields, body.get("reason"))
        elif action == "revert":
            cal.revert_change(body["change_id"])
        else:
            raise KeyError(action)
        self.snap.refresh()


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            if not self.path.startswith("/api/state"):
                super().log_message(fmt, *args)

        def _send(self, code, body, ctype="application/json", extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj).encode())

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/state":
                return self._send(200, app.snap.response())
            if path == "/api/config":
                return self._json(200, {"poll_ms": app.cfg.get("poll_ms", 1000), "modes": app.modes,
                                        "channels": app.cfg["demo"]["channels"]})
            if path.startswith("/media/clip/"):
                return self._clip(unquote(path[len("/media/clip/"):]))
            if path.startswith("/media/feed/"):
                return self._feed(unquote(path[len("/media/feed/"):]))
            if path.startswith("/static/"):
                f = (WEB / path[len("/static/"):]).resolve()
                if WEB in f.parents and f.is_file():
                    return self._send(200, f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream"))
                return self._send(404, b"not found", "text/plain")
            if path == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")
            # Every other path is a client-side route (e.g. /needs/T-12).
            return self._send(200, (WEB / "index.html").read_bytes(), TYPES[".html"])

        do_HEAD = do_GET

        def do_POST(self):
            path = urlparse(self.path).path
            m = re.fullmatch(r"/api/(control|edit)/(\w+)", path)
            if not m:
                return self._json(404, {"error": "not found"})
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                (app.control if m.group(1) == "control" else app.edit)(m.group(2), body)
                return self._json(200, {"ok": True})
            except NotImplementedError:
                return self._json(501, {"ok": False, "error": "this source has no such control"})
            except Exception as e:
                return self._json(502, {"ok": False, "error": f"{type(e).__name__}: {e}"})

        def _feed(self, feed_id):
            """Relay a feed marked "proxy": true (e.g. a camera bound to localhost on another port)
            so the browser only ever talks to the dashboard. Streams until either side closes."""
            feeds = (app.snap.raw.get("feeds") or {}).get("feeds", [])
            f = next((x for x in feeds if str(x.get("id")) == feed_id and x.get("proxy") and x.get("url")), None)
            if not f:
                return self._send(404, b"no proxied feed with that id", "text/plain")
            try:
                upstream = _feed_opener.open(f["url"], timeout=5)
            except Exception as e:
                return self._send(502, f"feed unreachable: {e}".encode(), "text/plain")
            with upstream:
                self.send_response(200)
                self.send_header("Content-Type", upstream.headers.get("Content-Type", "application/octet-stream"))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                try:
                    while True:
                        chunk = upstream.read1(64 * 1024) if hasattr(upstream, "read1") else upstream.read(64 * 1024)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass

        def _clip(self, name):
            fpath = app.adapters["cell"].clip_path(name)
            if not fpath:
                return self._send(404, b"clip not found", "text/plain")
            size = os.path.getsize(fpath)
            ctype = TYPES.get(Path(fpath).suffix, "application/octet-stream")
            rng = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
            with open(fpath, "rb") as f:
                if rng and (rng.group(1) or rng.group(2)):
                    if rng.group(1):
                        start = int(rng.group(1))
                        end = int(rng.group(2)) if rng.group(2) else size - 1
                    else:
                        start, end = max(0, size - int(rng.group(2))), size - 1
                    end = min(end, size - 1)
                    f.seek(start)
                    data = f.read(end - start + 1)
                    return self._send(206, data, ctype, {"Content-Range": f"bytes {start}-{end}/{size}",
                                                         "Accept-Ranges": "bytes"})
                return self._send(200, f.read(), ctype, {"Accept-Ranges": "bytes"})

    return Handler


def serve(cfg):
    app = App(cfg)
    app.snap.start()
    httpd = ThreadingHTTPServer((cfg["host"], cfg["port"]), make_handler(app))
    httpd.daemon_threads = True
    shown = "localhost" if cfg["host"] in ("0.0.0.0", "") else cfg["host"]
    print(f"Fleet & Field dashboard on http://{shown}:{cfg['port']}  (sources: "
          + ", ".join(f"{k}={v}" for k, v in app.modes.items()) + ")")
    print(f"Demo controls: http://{shown}:{cfg['port']}/control")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
