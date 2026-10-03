# Hyperion

<p align="left">
  <img alt="Isaac Sim" src="https://img.shields.io/badge/Isaac_Sim_6.0-76B900?style=for-the-badge&logo=nvidia&logoColor=white" />
  <img alt="Dell Pro Max GB10" src="https://img.shields.io/badge/Dell_Pro_Max_GB10-007DB8?style=for-the-badge&logo=dell&logoColor=white" />
  <img alt="vLLM" src="https://img.shields.io/badge/vLLM-30A2FF?style=for-the-badge" />
  <img alt="NemoClaw" src="https://img.shields.io/badge/NemoClaw-76B900?style=for-the-badge&logo=nvidia&logoColor=white" />
  <img alt="Docker" src="https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white" />
  <img alt="Discord" src="https://img.shields.io/badge/Discord-5865F2?style=for-the-badge&logo=discord&logoColor=white" />
</p>

**Finds the problem. Finds the person. Gets them there.**

Hyperion is an always-on operations supervisor that lives in your team's chat and runs entirely on one
box. Its first deployment is a 20-robot warehouse: NVIDIA Nova Carters working the aisles in Isaac Sim.
When a robot jams, overheats, runs low, or a shelf comes up short, a supervisor LLM spots it within
seconds, traces it to the root cause (the robot that stopped first, not the five queued behind it) and
opens a ticket with the evidence. A scheduler LLM turns that ticket into a plain-English card in Discord,
where the person on shift approves the fix with one ✅. A live command center shows the whole floor, the
tickets, and real camera video from inside the sim, down to a chase camera on any single robot. The
simulator, both models, the agents, the dashboard and the bot all run on a single Dell Pro Max GB10, and
no model call ever leaves the box.

**Runs on:** one Dell Pro Max GB10, 128 GB unified memory, nothing in the cloud
**Built for:** Dell Pro Max with GB10 Hackathon, 2026-10-03, Hult International Business School, Cambridge MA

<p align="center">
  <img src="docs/img/hero.gif" alt="Nova Carters driving through the pack bays in the Isaac Sim warehouse" width="760" />
</p>

---

## Snapshot

- Real Isaac Sim on the GB10 itself: 20 Nova Carters on aarch64, one-way traffic, junction locks,
  drive-through pack bays, and a red beacon over any robot in trouble
- Two local LLMs from one model server: Qwen3.6-35B-A3B (NVFP4) on vLLM runs both the supervisor agent
  and the scheduler bot
- A supervisor agent that can only touch its own tools: OpenClaw inside a NemoClaw sandbox, with 11 tools
  over the live fleet and thinking turned on
- Discord as the control room: incidents land in `#red-room` with an LLM-written debrief, decisions land
  in `#approvals`, and a ✅ or ❌ flows straight back into the ticket
- A live command center: flip between the floor map and real Isaac video, step through 24 feeds, click
  any robot for its chase camera with status, task and battery on the frame
- Code enforces, the model explains: detection, spacing, locks, ticket states and the commissioning gate
  are all code; the LLMs decide what to say, what to escalate and to whom
- Measured on the box, not assumed: fault detected in 2 to 3 s, root-cause ticket in 15 to 94 s, Discord
  cards about 4 s later, and 0 robot overlaps across 30 sim-minutes × 3 seeds

<p align="center">
  <img src="docs/img/deployment.png" alt="The GB10's own screen: Isaac Sim on the left, the command center on the right" width="900" /><br/>
  <sub>The submission as it runs: Isaac Sim and the command center side by side on the GB10's own screen.</sub>
</p>

<table>
  <tr>
    <td width="50%"><img src="docs/img/command-center.png" alt="Command center with the AMR-00 chase camera" /></td>
    <td width="50%"><img src="docs/img/isaac-cameras.png" alt="Four Isaac Sim camera angles" /></td>
  </tr>
  <tr>
    <td><sub>AMR-00's chase camera, live from Isaac, with its status on the frame.</sub></td>
    <td><sub>Fixed cameras: overview, aisle and pack bays, plus one chase camera per robot.</sub></td>
  </tr>
  <tr>
    <td colspan="2"><img src="docs/img/needs.png" alt="Tickets written by the supervisor LLM" /></td>
  </tr>
  <tr>
    <td colspan="2"><sub>Tickets written by the supervisor LLM: a deadlock is traced back to the robot that stopped first.</sub></td>
  </tr>
</table>

---

## Built With

<p align="left">
  <img alt="Python" src="https://img.shields.io/badge/Python_3.12-3776AB?style=flat-square&logo=python&logoColor=white" />
  <img alt="Isaac Sim" src="https://img.shields.io/badge/Isaac_Sim-76B900?style=flat-square&logo=nvidia&logoColor=white" />
  <img alt="OpenUSD" src="https://img.shields.io/badge/OpenUSD-333333?style=flat-square" />
  <img alt="vLLM" src="https://img.shields.io/badge/vLLM_(Qwen3.6_NVFP4)-30A2FF?style=flat-square" />
  <img alt="NemoClaw" src="https://img.shields.io/badge/NemoClaw_+_OpenClaw-76B900?style=flat-square&logo=nvidia&logoColor=white" />
  <img alt="discord.py" src="https://img.shields.io/badge/discord.py-5865F2?style=flat-square&logo=discord&logoColor=white" />
  <img alt="aiohttp" src="https://img.shields.io/badge/aiohttp-2C5BB4?style=flat-square&logo=aiohttp&logoColor=white" />
  <img alt="OpenCV" src="https://img.shields.io/badge/OpenCV-5C3EE8?style=flat-square&logo=opencv&logoColor=white" />
  <img alt="Docker Compose" src="https://img.shields.io/badge/Docker_Compose-2496ED?style=flat-square&logo=docker&logoColor=white" />
</p>

---

## What It Does

### For the people on shift
- See the whole floor at a glance: robots, batteries, deliveries per minute, open tickets
- Flip the live feed between the floor map and real camera video from the sim, or open any robot's own
  chase camera with its status on the frame
- Read incidents in plain English, not error codes: what broke, which orders are at risk, what to do
- Approve or reject a fix from Discord with one reaction and watch the ticket move to `rescheduled`
- Ask the supervisor anything about the floor ("which robots are waiting for traffic, and why?") and get
  an answer built from live telemetry

### Behind the scenes
- Nine detectors read live telemetry eight times a second: stuck robots (after 2 s), deadlock clusters,
  drive faults, overheating, low battery, inventory mismatches, low stock, stale telemetry and
  throughput drops; a robot that is only yielding to traffic gets a 15 s grace instead of a ticket
- The supervisor LLM is woken by a webhook, pulls what it needs through its tools, and opens one ticket
  per root cause; a second report of the same event or robot updates the open ticket instead of
  duplicating it
- Ticket states are enforced in code (`open → acknowledged → rescheduled → resolved`, plus `escalated`
  and `failed`), so neither model can skip a step or reopen a closed ticket
- navbot has its own LLM write the debrief and headline in under a second, posts the cards, and sends
  approvals back as ticket updates
- A new robot skill only goes live when a Thompson-sampling bandit is 95% sure its success rate is at
  least 80%, measured on real GR00T policy trials on the same box

---

## App Map

```text
navfix/
  fleetops/                      Integration: sim, Isaac, glue, deployment
    sim/fleet.py                   Fleet logic: Dijkstra routing on a one-way loop, junction locks,
                                   slot reservations, holding loop, inventory, shrink, faults
    sim/sim_bridge.py              Telemetry, events and the demo-fault queue (:3001)
    sim/pusher.py                  Pushes a fleet (Isaac or headless) to the bridge
    sim/fleet_runner.py            Headless sim for when Isaac is closed
    sim/test_fleet.py              30-sim-minute traffic test: overlaps, deadlocks, throughput
    isaac/warehouse_live.py        Isaac Sim warehouse, 20 Nova Carters, MJPEG cameras (:8212)
    glue/fleetops_glue.py          Dashboard sources + ticket forwarding to navbot (:7100)
    docker/                        One image, six services, compose with health checks
    fleetops.sh                    start | stop | status | isaac | headless | reset-demo | build
    tools/                         e2e.sh, ask_agent.sh, screen_layout.py, read_card.py
  supervisor/
    tools_service.py               Detectors, tickets, trust and commissioning (:8090)
    bandit.py                      Thompson-sampling commissioning gate
    tools.json                     The 11 tools the supervisor agent can call
    openclaw/                      Supervisor skill and agent instructions for OpenClaw
  navbot/                        Discord bot + scheduler LLM (:8787), see navbot/README.md
  dashboard/                     Command center (:8095), see dashboard/README.md
  docs/img/                      Screenshots and the demo GIF used here
```

---

## Project Details

- Hardware: one Dell Pro Max GB10 (Grace Blackwell, aarch64, 128 GB unified memory) runs everything
- Simulation: Isaac Sim 6.0 via pip on aarch64; the static warehouse is built offline into one USD file
  and referenced in a single step, robots are posed by the fleet logic with physics off on the chassis
- Models: `nvidia/Qwen3.6-35B-A3B-NVFP4` on vLLM, shared by the supervisor agent and the scheduler bot
- Supervisor agent: OpenClaw in a NemoClaw (OpenShell) sandbox; its only way out is its own tool service
- Services: bridge, headless sim, supervisor tools, glue, dashboard and navbot in one Docker image, with
  host networking, health checks and `restart: unless-stopped`
- Cameras: replicator RGB annotators encoded to JPEG and served as MJPEG, relayed by the dashboard
- Secrets: Discord token and shared keys live in owner-only env files, never in the image or in git
- Team: Ferbin (integration, Isaac Sim, fleet logic, supervisor LLM, deployment), Thiago (supervisor tool
  service and skill, business model), Megha (navbot, scheduler, command center), Amal (command center UI)

---

## Data Model

Everything flows through two records. An **event** is what the code saw: a type (`stuck`, `deadlock`,
`drive_fault`, `overheat`, `low_battery`, `inventory_mismatch`, `low_stock`, `telemetry_stale`,
`throughput_drop`), the robot or robots involved, the zone,
a severity and a timestamp. A **ticket** is what the supervisor decided to do about it: the `event_id`
it came from, `robot_id`, `zone`, a plain-English `reason`, `priority`, `needed_by`, the `evidence` the
model cited, an `assignee` and `eta` filled in by the scheduler side, and a `history` of every state
change with who made it and why. Tickets are written by the supervisor's tools only, state moves are
checked against an allow-list, and the glue forwards each new ticket to navbot as `TASK_FAILED` (plus
`APPROVAL_REQUEST` when the fix changes the schedule). Commissioning trials and the bandit's posterior
are kept in a separate trust store, so a skill's record survives restarts.

---

## Getting Started

### Prerequisites
- A Dell Pro Max GB10 (or DGX Spark) with Docker and the NVIDIA container runtime
- vLLM serving `nvidia/Qwen3.6-35B-A3B-NVFP4` on port 8000
- A NemoClaw sandbox running the OpenClaw gateway, with the skill from `supervisor/openclaw/`
- Isaac Sim 6.0 installed with pip into `~/isaacsim-env` (aarch64 wheels from pypi.nvidia.com)
- A Discord bot token and channel ids in `navbot/.env` (see `navbot/.env.example`)

### Install & Run

```bash
git clone -b fleetops-integration https://github.com/mochi-bunny/navfix
cd navfix/fleetops

./fleetops.sh start                # model check, six containers in order, agent check
./fleetops.sh isaac                # Isaac Sim on the screen becomes the live sim
python3 tools/screen_layout.py     # Isaac left, command center right -> http://localhost:8095
./fleetops.sh status
```

Try a fault end to end, or ask the supervisor a question:

```bash
tools/e2e.sh stuck                 # inject, then time detection -> ticket -> dashboard -> Discord
tools/ask_agent.sh "Which robots are waiting for traffic, and why?"
```

Full deployment notes are in [`fleetops/README.md`](./fleetops/README.md).

---

## Roadmap

### Shipped for the hackathon
- [x] Isaac Sim warehouse with 20 Nova Carters, running on the GB10 itself
- [x] Supervisor LLM with tools, root-cause tickets and code-enforced ticket states
- [x] Scheduler LLM and Discord cards with working ✅ / ❌ approvals
- [x] Command center with live map, 24 camera feeds and per-robot chase cameras
- [x] Commissioning gate on real GR00T policy trials
- [x] One-command Docker deployment with health checks

### Next
- [ ] Swap the adapter for other operations: facilities, hospitals, retail, utilities run the same loop
- [ ] Live calendars and travel time so the scheduler books a real person, not just a slot
- [ ] Bridge to real robots over ROS 2, with the sim kept as the rehearsal floor

---

## License

Built for the Dell Pro Max with GB10 Hackathon, 2026. Not for production use as-is.
