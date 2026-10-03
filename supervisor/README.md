# Supervisor tool service

The Warehouse Supervisor agent's tools. Code enforces, the model explains: detectors, event ids, order risk, ticket
states and the commissioning gate are computed here, and the agent only reads results and decides what to say and
where to route. Standard library Python, port 8090.

Live data path: the Isaac Sim warehouse (`warehouse_live.py --bridge http://<gb10>:3001` on the Isaac laptop) or its
headless twin `fleet_runner.py` pushes JSON (`fleet.py`'s telemetry, inventory, layout) about 8 times a second to
`sim_bridge.py` on port 3001; this service polls the bridge. Only one pusher at a time: stop `fleet_runner.py` when
Isaac connects.

| Piece | Where | Start |
|---|---|---|
| Sim bridge | `~/fleetops/sim_bridge.py`, port 3001 (all interfaces, push needs the token) | `tmux new -d -s bridge "bash -c 'cd ~/fleetops && BRIDGE_TOKEN=$(cat .bridge_token) python3 sim_bridge.py'"` |
| Isaac stand-in (no Isaac) | `fleet_runner.py`, run inside `cell` because the host has no numpy | `docker cp ~/fleetops cell:/fleetops && docker exec -d -e BRIDGE_TOKEN="$(cat ~/fleetops/.bridge_token)" cell bash -c "cd /fleetops && python fleet_runner.py --bridge http://127.0.0.1:3001 > /tmp/fleet_runner.log 2>&1"` |
| Old grid sim + dashboard (no longer read) | `~/warehouse`, port 3000 | `tmux new -d -s wh "bash -c 'cd ~/warehouse && python3 server.py 2>&1 \| tee srv.log'"` |
| This service | `~/hack/src/app`, port 8090 on 127.0.0.1 and 172.18.0.1 | `tmux new -d -s tools "bash -c 'cd ~/hack/src/app && python3 tools_service.py 2>&1 \| tee tools.log'"` |
| GR00T policy servers | `cell` container, ports 5555 (libero_10) and 5556 (libero_goal) | see the restart loop below; start them one at a time |
| LLM | `vllm-qwen` container, port 8000 | `docker start vllm-qwen` |
| Agent | NemoClaw sandbox `navfix` (OpenClaw), skill `openclaw/warehouse-supervisor` | `nemoclaw navfix skill install openclaw/warehouse-supervisor` |

The sandbox reaches this service as `http://host.openshell.internal:8090` (172.18.0.1) through the
`openclaw/warehouse-tools.yaml` preset (`nemoclaw navfix policy add --from-file ... --yes`). The preset allows reads,
`POST /tickets`, `POST /commission` and `PATCH /tickets/**` only, so the agent cannot reach `/demo/*` or the sim.

## What the sim must provide (keep these when replacing the sim)

| Endpoint | Fields used |
|---|---|
| `GET /api/telemetry` | `t` (sim seconds), `picks`, `deliveries`, `stale_s` (bridge), `robots[]` with `id, x, y` (m), `theta` (deg), `vel` (m/s), `task` (pick/carry/charge), `job_id`, `goal [x,y]`, `battery`, `temp`, `zone`, `carrying` (SKU string or null), `error` (DRIVE_FAULT/OVERHEAT/LOW_BATTERY/BIN_EMPTY or ""), `yielding`, `waiting_s` |
| `GET /api/layout` | `units: "metres"`, `aisles[] {zone, x}`, `cross_lanes {bottom_y, top_y}`; fetched once |
| `GET /api/inventory` | `zones[]` with `zone, total, expected_total, bins[]` of `{bin [x,y], sku, qty, expected}`; `qty` is physical stock, `expected` is the system count, and they differ only when stock goes missing |
| `POST /api/inject {robot, fault}`, `POST /api/clear`, `POST /api/shrink {zone, units}` | demo faults, forwarded from `/demo/*` |

How the service reads it:

- Only new samples count (a change in `t`), and every duration (stopped for, waiting, error windows, throughput) is in
  sim seconds, so a slow or bursty Isaac feed does not look like stopped robots.
- `vel` and `theta` are used as sent. A robot is stuck when it has somewhere to go, is not moving, and is not merely
  queuing for traffic (`yielding` with `waiting_s` under 15 s). A new goal resets the stop clock, so leaving a charge dock
  is not a stop.
- A jam is one `deadlock` event keyed on the robot that stopped first, with the robots queued behind it in its facts.
- Off-trajectory means off the lane map: inside the rack area, more than 0.8 m from every aisle centre line.
- Throughput drop is a Poisson tail test of the last 3 sim minutes against the zone's own earlier rate (about 1.4
  deliveries/min per zone is too few for a plain ratio).
- If `stale_s` passes 3 s (nobody pushing), the service raises one `telemetry_stale` event and freezes the robot
  detectors instead of reading frozen positions as stuck robots. If `t` jumps backwards, it treats it as a sim restart.
- Deliveries per zone and robot timelines are derived from successive samples; `location` comes from the lane map.

## Tickets: the contract with the Scheduler agent

States: `open` → `acknowledged` → `rescheduled` → `resolved`, plus `failed` (→ `escalated`) and `escalated`
(→ `acknowledged` or `resolved`). Illegal transitions return 400 with the allowed ones.

| Call | Who | Body |
|---|---|---|
| `POST /tickets` | supervisor | `{event_id, event_type, robot_id or zone, reason, needed_by, priority (low/medium/high/critical), evidence?}`; returns 201, or 200 with `duplicate: true` if a live ticket exists for that event |
| `GET /tickets?status=`, `GET /tickets/T-1` | both | |
| `PATCH /tickets/T-1` | both | `{actor: supervisor/scheduler/human, status?, note?, assignee?, eta?, needed_by?, priority?}` |

Webhooks (set as environment variables before starting the service): `SCHEDULER_WEBHOOK_URL` gets a POST when the
supervisor opens or changes a ticket; `SUPERVISOR_WEBHOOK_URL` (+ `SUPERVISOR_WEBHOOK_TOKEN`, sent as a bearer token)
gets a POST when the scheduler or a human changes one, and when commissioning finishes. Body:
`{"message": "TICKET T-1 open | stuck | robot 7 | ...", "ticket": {...}}`. `GET /notifications` shows what was sent.

## Commissioning (trust gate)

`POST /commission {subject}` starts a background bandit run and returns `C-n`. `GET /commission/latest` shows one
`line` per batch while it runs (CORS is open, so a dashboard can poll it). `GET /trust` gives each subject's evidence and
`trusted`, which is true only when P(success rate ≥ 0.8) ≥ 0.95. Subjects are in `CATALOG` in `tools_service.py`:
`drawer_skill` and `soup_sauce_skill` run real GR00T N1.7 episodes in LIBERO; `route_b` flips coins at fixed rates.
Clips from GR00T trials are served at `/clips/C-n/<policy>/<file>.mp4`.

## Autopilot: when code wakes the agent

The service wakes the agent through OpenClaw's `POST /hooks/agent` (host forward `127.0.0.1:18789`, bearer token in
`data/hooks.token`, set in the sandbox with `nemoclaw navfix config set --key hooks ...`). It wakes it for:

- a digest of new problems that are at least 5 s old, not ticketed, and not handed over in the last 5 minutes
  (checked every 30 s; a type with 4+ events is grouped as one systemic line);
- ticket changes made by the Scheduler or a human, and finished commissioning runs;
- a fleet brief every `BRIEF_EVERY_S` seconds (0 = off).

Each wake runs in its own session (`hook:<name>:<n>`): OpenClaw drops a hook run whose session is still busy. Start the
service with `SUPERVISOR_WEBHOOK_URL=http://127.0.0.1:18789/hooks/agent SUPERVISOR_THINKING=off`, and add
`SUPERVISOR_DELIVER_TO=channel:<discord channel id>` to post the agent's replies. Toggle at runtime:
`curl -s -X POST localhost:8090/demo/autopilot -H 'Content-Type: application/json' -d '{"triage": false, "brief_every_s": 600}'`.
The sandbox workspace `AGENTS.md` starts with the supervisor's standing orders (`openclaw/AGENTS-supervisor.md`;
the original is `AGENTS.md.orig`).

## Demo controls (presenter only)

```sh
curl -s -X POST localhost:8090/demo/inject -H 'Content-Type: application/json' -d '{"robot": 7, "fault": "stuck"}'
curl -s -X POST localhost:8090/demo/shrink -H 'Content-Type: application/json' -d '{"zone": 3, "units": 6}'
curl -s -X POST localhost:8090/demo/clear
```

Reset tickets and trust before a run: stop the service, delete `data/tickets.json` and `data/trust.json`, start it again.

## Restarting the GR00T servers

Start them one at a time: on GB10's unified memory, two loading at once ran out of CUDA memory.

```sh
for pair in "libero_10 5555" "libero_goal 5556"; do set -- $pair
  docker exec -d cell bash -c "python gr00t/eval/run_gr00t_server.py --model-path /ckpt/GR00T-N1.7-LIBERO/$1 \
    --embodiment-tag LIBERO_PANDA --use-sim-policy-wrapper --host 127.0.0.1 --port $2 > /tmp/gr00t_$1.log 2>&1"
  until ss -ltn | grep -q ":$2 "; do sleep 3; done
done
```
