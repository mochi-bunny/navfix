"""Pushes fleet state to sim_bridge.py and applies the demo commands it hands back.

Used by fleet_runner.py (headless) and by the Isaac sim (warehouse_live.py). Sends run on a
background thread so a slow network never stalls the simulation; if a push is still in flight
the next one is skipped rather than queued.
"""
import json, threading, time, urllib.request


class Pusher:
    def __init__(self, fleet, url, token="", hz=8.0, source="fleet"):
        self.fleet, self.url, self.token = fleet, url.rstrip("/") + "/push", token
        self.period, self.source = 1.0 / hz, source
        self.busy, self.last = False, 0.0
        self.pending = []           # commands received, applied on the sim thread in apply()
        self.results = []           # command results, reported on the next push
        self.lock = threading.Lock()
        self.sent = self.failed = 0
        self.last_error = ""
        self.layout_sent = False
        self.events_sent = 0        # index into fleet.events already delivered to the bridge

    def tick(self, now=None):
        """Call from the sim loop every step. Snapshots state and pushes at most hz times a second."""
        now = time.time() if now is None else now
        if self.busy or now - self.last < self.period:
            return
        self.last, self.busy = now, True
        body = {"source": self.source, "telemetry": self.fleet.telemetry(), "inventory": self.fleet.inventory()}
        evs = getattr(self.fleet, "events", None)
        if evs is not None:   # the fleet's event log, incrementally: only what the bridge has not seen yet
            body["events"], body["events_upto"] = evs[self.events_sent:], len(evs)
        if not self.layout_sent:
            body["layout"] = self.fleet.layout()
        with self.lock:
            body["results"], self.results = self.results, []
        threading.Thread(target=self._send, args=(body,), daemon=True).start()

    def _send(self, body):
        try:
            req = urllib.request.Request(self.url, data=json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": "application/json",
                                                  "Authorization": f"Bearer {self.token}"})
            with urllib.request.urlopen(req, timeout=3) as r:
                cmds = json.loads(r.read() or b"{}").get("commands", [])
            with self.lock:
                self.pending.extend(cmds)
            self.layout_sent = self.layout_sent or "layout" in body
            if "events_upto" in body:
                self.events_sent = body["events_upto"]
            self.sent += 1
        except Exception as e:  # network blips are expected on venue Wi-Fi: keep simulating
            self.failed += 1
            self.last_error = repr(e)[:120]
            with self.lock:     # results were not delivered: send them again next time
                self.results = body.get("results", []) + self.results
        finally:
            self.busy = False

    def apply(self):
        """Call from the sim loop: runs queued demo commands against the fleet on the sim thread."""
        with self.lock:
            cmds, self.pending = self.pending, []
        for c in cmds:
            res = self.fleet.command(c)
            with self.lock:
                self.results.append(dict(res, command=c.get("kind"), t=round(self.fleet.t, 1)))
        return cmds
