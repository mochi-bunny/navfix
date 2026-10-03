# FleetOps: a warehouse fleet run by two local LLMs on one Dell Pro Max GB10

Twenty NVIDIA Nova Carter robots work a warehouse in Isaac Sim. A **supervisor LLM** watches the fleet,
decides what needs a person, and opens tickets. A **scheduler side in Discord** turns those tickets into
cards and approvals for the people on shift, with its own LLM writing the plain-English debriefs. A live
command-center dashboard shows all of it. Everything runs on this box: no model call leaves it.

Design rule: **code enforces, models explain.** Detection, spacing, junction locks, ticket states and the
commissioning gate are computed in code; the LLMs decide what to say, what to escalate, and to whom.

## What runs where

```
 Isaac Sim GUI (this screen) ─┐                        ┌─> dashboard :8095  (this screen, and the LAN)
   or headless sim container ─┼─push─> sim bridge :3001 ┤
                              │        (telemetry,      └─> supervisor tool service :8090 ──ticket──> glue :7100
                              │         events, faults)       │  detectors, tickets,                    │   │
                              │                               │  trust gate (bandit)                     │   └─> navbot :8787 ─> Discord
                              │                               └─wake─> SUPERVISOR LLM (OpenClaw agent,   │        #red-room #approvals
                              │                                        NemoClaw sandbox "navfix")        │        #monitor-bot
                              └──────────── demo faults (inject / clear / shrink) <──────────────────────┘   ✅/❌ ─> ticket updates
 Both LLMs: Qwen3.6-35B-A3B NVFP4 on vLLM (container vllm-qwen, :8000), local.
```

| Part | Who built it | Runs as | Port |
|---|---|---|---|
| Fleet logic: one-way traffic, junction locks, inventory, faults, JSON event log | integration | `sim/fleet.py` (pure Python) | - |
| Isaac Sim warehouse, 20 Nova Carters, fault beacons | integration | `isaac/warehouse_live.py` on this screen | - |
| Headless sim (when Isaac is not open) | integration | container `sim` | - |
| Sim bridge | integration | container `bridge` | 3001 |
| Supervisor tool service + skill | Thiago (handover), integration fixes | container `supervisor` | 8090 |
| **Supervisor LLM** | Thiago | OpenClaw in NemoClaw sandbox `navfix`, thinking on | 18789 |
| Glue: dashboard sources, Discord forwarding, live fleet log | integration | container `glue` | 7100 |
| Dashboard (command center) | Megha, Amal (UI) | container `dashboard` | 8095 |
| navbot + **scheduler-side LLM** (debriefs, headlines) | Megha | container `navbot` | 8787 |
| Model | - | container `vllm-qwen` | 8000 |

## Run it

```bash
cd ~/fleetops
./fleetops.sh start         # model check, then the six containers in dependency order, then the agent check
./fleetops.sh isaac         # Isaac Sim GUI on this screen becomes the sim (the headless sim stops)
python3 tools/screen_layout.py   # Isaac left, dashboard right
./fleetops.sh status
./fleetops.sh headless      # close Isaac, headless sim back
./fleetops.sh reset-demo    # back up and clear tickets + trust history before a run
./fleetops.sh build         # after a code change: rebuild the image and redeploy
```

Containers restart on their own after a reboot (`restart: unless-stopped`). Secrets live in
`secrets/fleetops.env` and `~/navfix/navbot/.env` (owner-only, never in the image or in git).

## Demo faults

From the dashboard's control page, or:

```bash
curl -X POST localhost:8090/demo/inject -H 'Content-Type: application/json' -d '{"robot": 7, "fault": "stuck"}'     # or overheat / low_battery
curl -X POST localhost:8090/demo/shrink -H 'Content-Type: application/json' -d '{"zone": 2, "units": 6}'          # stock goes missing
curl -X POST localhost:8090/demo/clear
```

Measured on this box (containerised stack): fault detected in **2 s**; the supervisor LLM opens a ticket in
**15-75 s** (longer when a jam forms and it reasons about the root cause); the dashboard shows it immediately;
Discord cards follow within **4 s**.

## Checks

```bash
tools/e2e.sh stuck              # fault -> detection -> ticket -> dashboard -> Discord, timed
tools/ask_agent.sh "How many robots are waiting for traffic?"   # supervisor LLM Q&A
python3 sim/test_fleet.py 1800 7  # 30 sim-minutes of traffic: overlaps, deadlocks, throughput
curl localhost:7100/fleetlog?minutes=5   # live fleet log (JSON) for the scheduler side
```
