"""Stand-in for the supervisor bot.
  python mock_supervisor.py listen          # prints approval decisions (port 8788)
  python mock_supervisor.py send refusal    # sends one sample event to navbot
  python mock_supervisor.py send all        # sends every sample in order
  python mock_supervisor.py send red        # sends every #red-room variant
  python mock_supervisor.py send dispatch   # sends every #dispatch-recall card
  python mock_supervisor.py telemetry       # sends one hourly telemetry CSV
  python mock_supervisor.py fleetlog [file] # sends a JSON fleet log (default samples/fleet_log_5min.json)
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

from dotenv import load_dotenv

load_dotenv()

BOT_URL = os.getenv("BOT_URL", "http://127.0.0.1:8787/events")
TELEMETRY_URL = BOT_URL.rsplit("/", 1)[0] + "/telemetry"

# Hourly telemetry: one row per robot. GR1-02 is not in use, so it's left off the card.
TELEMETRY_CSV = """robot,site,status,in_use,battery_pct,temp_c,errors_1h,last_seen
GR1-07,Globex,running,1,82,41,0,{t}
GR1-03,Initech,degraded,1,64,73,2,{t}
GR1-11,Acme,offline,1,,,,2026-10-03T12:41:00-04:00
GR1-05,Globex,idle,1,15,38,0,{t}
GR1-09,Acme,running,1,91,44,0,{t}
GR1-02,Initech,idle,0,100,30,0,{t}
"""
KEY = os.getenv("BOT_SHARED_KEY", "change-me")

SAMPLES = {
    "refusal": {
        "event": "SKILL_REFUSED", "ticket_id": "T-12", "site": "Globex (Kendall Square)",
        "cell": "kendall-1", "robot": "GR1-07", "error_code": "GATE-FAIL-LOW-PASS", "skill": "open_middle_drawer", "passed": 1, "run": 10,
        "deadline": "17:00", "clips": [],
    },
    "refusal_min": {
        "event": "SKILL_REFUSED", "site": "Acme (Back Bay)", "skill": "pick_item", "passed": 0, "run": 8,
    },
    "refusal_full": {
        "event": "SKILL_REFUSED", "ticket_id": "T-14", "site": "Initech (Seaport)",
        "cell": "seaport-2", "robot": "GR1-03", "error_code": "E-GRIP-DROP",
        "skill": "place_in_basket", "passed": 5, "run": 12, "deadline": "16:30",
        "gate": "≥80% success at 95% confidence",
        "failure_note": "Gripper drops the item when the basket is more than half full.",
    },
    "failed": {
        "event": "TASK_FAILED", "ticket_id": "T-15", "site": "Globex (Kendall Square)",
        "cell": "kendall-1", "robot": "GR1-07", "error_code": "E-GRIP-SLIP",
        "debrief": "During order 1043 the robot picked two items cleanly, then lost grip on the "
                   "third as it lifted it out of the drawer. The job stopped and the item was left in the drawer.",
        "skill": "pick_item", "passed": 2, "run": 3, "deadline": "18:00",
        "failure_note": "Item slipped from the gripper on the third pick during order 1043.",
    },
    "failed_min": {
        "event": "TASK_FAILED", "site": "Acme (Back Bay)", "skill": "open_middle_drawer", "robot": "GR1-11",
        "passed": 0, "run": 1,
    },
    "book": {
        "event": "APPROVAL_REQUEST", "request_id": "apr-book", "kind": "BOOK_VISIT", "ticket_id": "T-12",
        "context": ["You're in Kendall for the 2:30 Globex pitch.",
                    "Commissioning fits 3:00–3:45, before Initech at 4:00."],
        "options": [{"emoji": "✅", "choice": "book", "label": "Book it"},
                    {"emoji": "❌", "choice": "decline", "label": "Decline"}],
    },
    "replan": {
        "event": "APPROVAL_REQUEST", "request_id": "apr-replan", "kind": "REPLAN",
        "context": ["Red Line delay +12 min (MBTA alert).", "Leaving at 1:55 now arrives 2:37."],
        "options": [{"emoji": "1️⃣", "choice": "leave_now", "label": "Leave now (arrive 2:20)"},
                    {"emoji": "2️⃣", "choice": "taxi", "label": "Taxi at 1:55 (~16 min)"}],
    },
    "eta": {"event": "ETA_UPDATE", "ticket_id": "T-12", "site": "Globex",
            "engineer": "Field engineer", "eta": "2:20", "reason": "Red Line delay, leaving early"},
    "gate": {"event": "GATE_OPEN", "site": "Globex (Kendall Square)", "cell": "kendall-1", "robot": "GR1-07",
             "ticket_id": "T-12", "skill": "open_middle_drawer", "policy": "B",
             "passed": 13, "run": 13, "confidence_pct": 96, "total_trials": 24, "baseline_trials": 40},
    "dispatched": {"event": "JOB_DISPATCHED", "order_id": "1043", "site": "Globex (Kendall Square)",
                   "cell": "kendall-1", "robot": "GR1-07", "ticket_id": "T-12",
                   "skills": ["open_middle_drawer", "pick_item", "place_in_basket"]},
    "completed": {"event": "JOB_COMPLETED", "order_id": "1043", "site": "Globex (Kendall Square)",
                  "cell": "kendall-1", "robot": "GR1-07", "ticket_id": "T-12",
                  "skills": ["open_middle_drawer", "pick_item", "place_in_basket"], "duration_s": 161},
    "daily": {
        "event": "DAILY_SUMMARY", "date": "Saturday, Oct 3",
        "counts": {"events": 6, "sales pitches": 3},
        "events": [
            {"time": "09:00", "title": "Standup", "place": "Seaport"},
            {"time": "10:30", "title": "Acme pitch", "place": "Back Bay",
             "leave_by": "9:55", "mode": "transit", "minutes": 25},
        ],
        "tightest": "Back Bay → Kendall, leave by 1:55",
        "free": ["1:00–1:55", "4:30–7:00"],
        "weather": "Rain after 3pm, take transit",
        "fleet": ["Acme cell ready ✅", "Globex can't do drawer tasks yet ⛔ (T-12)"],
    },
    "infra": {"event": "INFRA_ERROR", "source": "gr00t-5556", "message": "policy server not responding"},
}
ORDER = ["daily", "refusal", "book", "replan", "eta", "gate", "dispatched", "completed"]
GROUPS = {
    "all": ORDER,
    "red": ["refusal", "refusal_min", "refusal_full", "failed", "failed_min"],
    "dispatch": ["gate", "dispatched", "completed"],
}


def send(name: str) -> None:
    ev = dict(SAMPLES[name])
    if ev["event"] == "APPROVAL_REQUEST":
        ev["request_id"] = f"{ev['request_id']}-{int(time.time())}"  # unique each run
    req = urllib.request.Request(
        BOT_URL, data=json.dumps(ev).encode(),
        headers={"Content-Type": "application/json", "X-Bot-Key": KEY},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(name, resp.status, resp.read().decode())
    except urllib.error.HTTPError as exc:
        print(name, exc.code, exc.read().decode())


def send_fleetlog(path: str = "samples/fleet_log_5min.json") -> None:
    with open(path, "rb") as f:
        body = f.read()
    req = urllib.request.Request(
        TELEMETRY_URL, data=body, headers={"Content-Type": "application/json", "X-Bot-Key": KEY},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            print("fleetlog", resp.status, resp.read().decode())
    except urllib.error.HTTPError as exc:
        print("fleetlog", exc.code, exc.read().decode())


def send_telemetry() -> None:
    from datetime import datetime
    body = TELEMETRY_CSV.format(t=datetime.now().astimezone().isoformat(timespec="seconds"))
    req = urllib.request.Request(
        TELEMETRY_URL, data=body.encode(), headers={"Content-Type": "text/csv", "X-Bot-Key": KEY},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print("telemetry", resp.status, resp.read().decode())
    except urllib.error.HTTPError as exc:
        print("telemetry", exc.code, exc.read().decode())


def listen() -> None:
    from aiohttp import web

    async def decision(request):
        print("DECISION:", json.dumps(await request.json(), indent=2))
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_post("/decisions", decision)
    web.run_app(app, host="127.0.0.1", port=8788)


if __name__ == "__main__":
    if sys.argv[1:2] == ["listen"]:
        listen()
    elif sys.argv[1:2] == ["telemetry"]:
        send_telemetry()
    elif sys.argv[1:2] == ["fleetlog"]:
        send_fleetlog(*sys.argv[2:3])
    elif sys.argv[1:2] == ["send"] and len(sys.argv) == 3:
        for n in GROUPS.get(sys.argv[2], [sys.argv[2]]):
            send(n)
            time.sleep(1.5)
    else:
        print(__doc__)
