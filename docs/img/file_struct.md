# Hyperion / navfix — codebase rundown

## What it is

Twenty simulated Nova Carter robots work a warehouse. A **supervisor LLM agent** watches live telemetry, decides what needs a human, and opens tickets. A **scheduler bot** (navbot, in Discord) turns tickets into cards people approve with a reaction. Both agents call the same local model (Qwen3.6-35B-A3B-NVFP4 on vLLM). A dashboard shows all of it live. Design rule: **code enforces, models explain** — detection, state transitions and the commissioning gate are plain Python; the LLMs only decide what to say and to whom.

## Architecture

```
 Isaac Sim GUI ─┐                        ┌─> dashboard :8095  (command center)
  or headless ──┼─push─> sim_bridge :3001┤
    sim container│       (telemetry,     └─> supervisor tool service :8090 ──ticket──> glue :7100
                 │        events, faults)       │  detectors, tickets,                    │   │
                 │                               │  commissioning gate (bandit)             │   └─> navbot :8787 ─> Discord
                 │                               └─wake─> SUPERVISOR LLM (OpenClaw agent,   │        #red-room #approvals
                 │                                        NemoClaw sandbox)                 │
                 └──────── demo faults (inject/clear/shrink) <───────────────────────────────┘   ✅/❌ ─> ticket updates
 Both LLMs: Qwen3.6-35B-A3B-NVFP4 on vLLM (container vllm-qwen, :8000), local only.
```

| Component | Port | File(s) |
|---|---|---|
| Fleet physics (pure Python) | — | [`fleetops/sim/fleet.py`](fleetops/sim/fleet.py) |
| Headless runner / Isaac scene | — | [`fleetops/sim/fleet_runner.py`](fleetops/sim/fleet_runner.py), [`fleetops/isaac/warehouse_live.py`](fleetops/isaac/warehouse_live.py) |
| Sim bridge | 3001 | [`fleetops/sim/sim_bridge.py`](fleetops/sim/sim_bridge.py) |
| Supervisor tool service + commissioning gate | 8090 | [`supervisor/tools_service.py`](supervisor/tools_service.py), [`supervisor/bandit.py`](supervisor/bandit.py) |
| Supervisor LLM (OpenClaw agent, sandboxed) | 18789 | [`supervisor/openclaw/`](supervisor/openclaw/), [`supervisor/tools.json`](supervisor/tools.json) |
| Glue (wires everything, forwards to Discord) | 7100 | [`fleetops/glue/fleetops_glue.py`](fleetops/glue/fleetops_glue.py) |
| Dashboard (command center) | 8095 | [`dashboard/`](dashboard/) |
| navbot + scheduler LLM (Discord) | 8787 | [`navbot/`](navbot/) |
| Model server | 8000 | [`fleetops/docker/compose.yaml`](fleetops/docker/compose.yaml) |

## The data flow, step by step

1. **Simulation produces state.** [`fleet.py`](fleetops/sim/fleet.py) runs the warehouse logic (one-way traffic, junction locks, picks, charging, inventory) as pure Python with no Isaac dependency. [`fleet_runner.py`](fleetops/sim/fleet_runner.py) drives it headless in real time; [`warehouse_live.py`](fleetops/isaac/warehouse_live.py) renders the same logic in Isaac Sim when the GUI is open. Either one pushes JSON to the bridge via [`pusher.py`](fleetops/sim/pusher.py).

2. **The bridge holds the latest state.** [`sim_bridge.py`](fleetops/sim/sim_bridge.py) is a standard-library HTTP server: `/push` receives state, `/telemetry` `/layout` `/inventory` serve it, and it queues demo commands (`inject`/`clear`/`shrink`) for the sim to pick up on its next push. If the pusher goes quiet, it keeps serving the last known state and reports `stale_s`.

3. **Code detects problems, not the LLM.** [`tools_service.py`](supervisor/tools_service.py) polls the bridge 8×/second and runs nine detectors — stuck, deadlock, drive fault, overheat, low battery, inventory mismatch, low stock, stale telemetry, throughput drop (`cond[("overheat", rid)] = ...` etc., [tools_service.py:415-547](supervisor/tools_service.py#L415-L547)). Each becomes a stable **event** (`E-0001`, with evidence) that clears itself once the underlying condition is gone.

4. **The supervisor LLM is woken, not polled.** A webhook fires when a new event appears. It's an OpenClaw agent running inside a NemoClaw/OpenShell sandbox, reasoning ("thinking on") over the 11 tools in [`tools.json`](supervisor/tools.json): `get_fleet_state`, `get_robot`, `get_zone_inventory`, `get_metrics`, `list_events`, `open_ticket`, `patch_ticket`, `list_tickets`, `get_trust`, `run_commission`, `get_commission`. Its standing orders live in [`supervisor/openclaw/AGENTS-supervisor.md`](supervisor/openclaw/AGENTS-supervisor.md) and [`supervisor/openclaw/warehouse-supervisor/SKILL.md`](supervisor/openclaw/warehouse-supervisor/SKILL.md). It decides *whether* to open a ticket and writes the `reason`; code attaches the evidence automatically.

5. **Tickets are a code-enforced state machine.** `open_ticket`/`patch_ticket` in [tools_service.py:687-750](supervisor/tools_service.py#L687-L750) only allow the transitions in `TICKET_FLOW`:
   ```
   open → {acknowledged, escalated, failed, resolved}
   acknowledged → {rescheduled, resolved, escalated, failed}
   rescheduled → {resolved, escalated, failed}
   failed → {escalated}
   escalated → {acknowledged, resolved}
   resolved → (terminal)
   ```
   A repeat report of the same event/subject returns the existing ticket (`duplicate: true`) instead of opening a new one — this is the dedup logic I tested earlier.

6. **Glue wires the pieces without touching them.** [`fleetops_glue.py`](fleetops/glue/fleetops_glue.py) polls the bridge and the tool service and serves the dashboard's live sources (`/clock /warehouse /reliability /commissioning /feeds /needs /events /messages`), plus a live map SVG and the fleet-log JSON format. It also receives the ticket webhook and forwards to navbot, and receives navbot's approve/deny decisions and forwards them back to the supervisor.

7. **navbot turns a ticket into a Discord card, and a reaction into a decision.** [`bot.py`](navfix/navbot/bot.py) posts cards to `#red-room`/`#approvals`; [`approvals.py`](navfix/navbot/approvals.py) builds the embed and tracks its status (✅/❌/📋 icons). `on_raw_reaction_add` ([bot.py:301](navfix/navbot/bot.py#L301)) is the only path from a human back into the system — it POSTs to `SUPERVISOR_DECISION_URL` with a shared-key header, and the result becomes a ticket update. [`llm.py`](navfix/navbot/llm.py) is the **second** LLM call site: it writes the one-sentence debrief for each card from a fixed glossary of event types, explicitly **never a source of facts** — note at [llm.py:1](navfix/navbot/llm.py#L1).

8. **The dashboard reads, never writes.** [`dashboard/server/app.py`](dashboard/server/app.py) serves `/api/state`; adapters in [`dashboard/server/adapters/`](dashboard/server/adapters/) (`mock.py`, `live.py`, `navbot.py`) switch per-source between recorded fixtures and live glue/navbot data, so a down source shows "offline" with its last good data instead of going blank. The browser side ([`dashboard/web/`](dashboard/web/)) is plain JS/CSS views, one file per panel (`fleetstatus.js`, `robots.js`, `gates.js`, `discord.js`, etc.).

9. **The commissioning gate decides when a new skill or route is trusted.** [`bandit.py`](navfix/supervisor/bandit.py) runs Thompson sampling: each candidate policy is an "arm," trials run in batches, and the gate opens only once `P(success rate ≥ 80%) ≥ 95%` for the best arm (`p_at_least`, [bandit.py:16](navfix/supervisor/bandit.py#L16)). `sim_runner` emulates trials as weighted coin flips for routes/testing; `gr00t_runner` drives real LIBERO rollouts through GR00T policy servers for robot skills. `run_commission`/`get_commission` in `tools.json` expose this to the supervisor agent as a background job.

## Everything else

| Area | Files | Role |
|---|---|---|
| Log format / replay | [`fleetops/sim/make_fleet_log.py`](fleetops/sim/make_fleet_log.py), `*/samples/fleet_log_5min.json` | Standalone JSON export of a sim run (meta, events, periodic snapshots) — what I analyzed when you asked about the fleet log earlier |
| Fleet-wide test | [`fleetops/sim/test_fleet.py`](fleetops/sim/test_fleet.py) | Headless stress test: collisions, stuck alarms, deliveries over N sim-seconds |
| Operator scripts | [`fleetops/fleetops.sh`](fleetops/fleetops.sh), [`fleetops/tools/e2e.sh`](fleetops/tools/e2e.sh), [`screen_layout.py`](fleetops/tools/screen_layout.py), [`ask_agent.sh`](fleetops/tools/ask_agent.sh) | Start/stop the 6-container stack, timed fault→ticket→Discord check, screen layout for the demo, CLI Q&A against the supervisor |
| Reasoning-mode wiring | [`fleetops/thinking.patch.json5`](fleetops/thinking.patch.json5), [`enable_thinking.sh`](fleetops/enable_thinking.sh), [`toolsearch-off.patch.json5`](fleetops/toolsearch-off.patch.json5) | Qwen-specific chat-template config patched into the OpenClaw sandbox — the piece that breaks if you swap the model, per our earlier discussion |
| Docker deployment | [`fleetops/docker/`](fleetops/docker/) | compose file (6 services, host networking, secrets via env file) + Dockerfile |
| navbot internals | [`fleetlog.py`](navfix/navbot/fleetlog.py), [`formatter.py`](navfix/navbot/formatter.py), [`store.py`](navfix/navbot/store.py), [`telemetry.py`](navfix/navbot/telemetry.py), [`mock_supervisor.py`](navfix/navbot/mock_supervisor.py) | Fleet-log formatting for Discord, SQLite storage, hourly telemetry health check, a standalone supervisor mock for testing navbot alone |

## What I added (branch `proxy-audit`, uncommitted)

Not part of Hyperion's demo — separate experiments from this conversation:

- [`proxy_audit/`](proxy_audit/) — the algorithmacy-lab Φ-vs-CRQA audit module (episode recorder, binarizer, TPM inference, Φ adapter, CRQA, stats, synthetic test fixture, tests). See the earlier writeup for what it does and why it currently can't run against real data.
- [`fleetops/tools/early_warning_eval.py`](fleetops/tools/early_warning_eval.py) — the offline fleet-log analysis that found the empty-bin dispatch race (48 robot-seconds saved, 0.8% of fleet time) and the null result on traffic-wait-predicts-lateness.

Nothing here is wired into `tools_service.py`, the dashboard, or navbot — they're standalone scripts I ran against recorded/synthetic data.
