#!/usr/bin/env python3
"""Sim bridge: runs on the GB10, serves the fleet sim contract to the tool service.

The fleet is simulated elsewhere (the Isaac Sim laptop, or fleet_runner.py anywhere). That process
POSTs its state to /push; this bridge serves the latest state on the endpoints the tool service
reads (see the README in ~/hack/src/app), and queues demo commands (inject / clear / shrink)
that it hands back in the next /push response. If the pusher drops off the network, the tool
service keeps getting the last state; every response carries stale_s so nobody is fooled.

  BRIDGE_TOKEN=... python3 sim_bridge.py          # port 3001 on all interfaces
  then start the tool service with SIM_URL=http://127.0.0.1:3001

Standard library only.
"""
import json, os, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

PORT = int(os.environ.get("BRIDGE_PORT", "3001"))
TOKEN = os.environ.get("BRIDGE_TOKEN", "")
LOCK = threading.Lock()
STATE = {"telemetry": None, "inventory": None, "layout": None, "pushed_at": None, "source": None, "pushes": 0}
COMMANDS = []                       # waiting to be handed to the sim on its next push
EVENTS = []                         # the fleet's event log, newest last (see fleet.py Fleet.log)
EVENT_SEQ = [0]                     # every stored event gets a bridge-wide sequence number
MAX_EVENTS = 20000
RESULTS = []                        # what the sim reported back for each command


def stale_s():
    return None if STATE["pushed_at"] is None else round(time.time() - STATE["pushed_at"], 2)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body):
        b = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        p = urlparse(self.path).path
        with LOCK:
            if p == "/health":
                return self.send(200, {"ok": STATE["telemetry"] is not None, "stale_s": stale_s(),
                                       "source": STATE["source"], "pushes": STATE["pushes"],
                                       "pending_commands": len(COMMANDS), "recent_results": RESULTS[-5:]})
            if p == "/api/events":
                # GET /api/events?since=<seq>: events after that sequence number, oldest first (max 2000)
                q = dict(x.split("=", 1) for x in urlparse(self.path).query.split("&") if "=" in x)
                since = int(q.get("since", 0) or 0)
                out = [e for e in EVENTS if e["seq"] > since][:2000]
                return self.send(200, {"events": out, "last_seq": EVENT_SEQ[0], "stale_s": stale_s()})
            key = {"/api/telemetry": "telemetry", "/api/inventory": "inventory", "/api/layout": "layout"}.get(p)
            if key is None:
                return self.send(404, {"error": "not found"})
            if STATE[key] is None:
                return self.send(503, {"error": "no state pushed yet"})
            return self.send(200, dict(STATE[key], stale_s=stale_s()))

    def do_POST(self):
        p = urlparse(self.path).path
        try:
            body = self.body()
        except ValueError:
            return self.send(400, {"error": "bad json"})
        if p == "/push":
            if TOKEN and self.headers.get("Authorization", "") != f"Bearer {TOKEN}":
                return self.send(401, {"error": "bad token"})
            with LOCK:
                for k in ("telemetry", "inventory", "layout"):
                    if body.get(k) is not None:
                        STATE[k] = body[k]
                STATE["pushed_at"], STATE["source"] = time.time(), body.get("source")
                STATE["pushes"] += 1
                RESULTS.extend(body.get("results", []))
                for e in body.get("events") or []:
                    EVENT_SEQ[0] += 1
                    EVENTS.append(dict(e, seq=EVENT_SEQ[0], wall=round(time.time(), 2)))
                del EVENTS[:-MAX_EVENTS]
                del RESULTS[:-50]
                out, COMMANDS[:] = list(COMMANDS), []
            return self.send(200, {"commands": out})
        kinds = {"/api/inject": "inject", "/api/clear": "clear", "/api/shrink": "shrink"}
        if p in kinds:
            with LOCK:
                COMMANDS.append(dict(body, kind=kinds[p], queued_at=time.time()))
            return self.send(200, {"ok": True, "queued": True})
        return self.send(404, {"error": "not found"})


if __name__ == "__main__":
    print(f"sim bridge on 0.0.0.0:{PORT}" + (" (push token required)" if TOKEN else " (no push token)"), flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
