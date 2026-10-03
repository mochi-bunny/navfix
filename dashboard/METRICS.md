# Dashboard metrics and where navbot's data fits

This page lists every number the dashboard shows, how it's calculated, and whether the Discord bot (navbot) can supply it.

The dashboard calculates very little itself. `server/snapshot.py` says: *"The UI only formats what it is given; it computes nothing."*
Most numbers arrive ready-made from a source (tickets, cell, calendar, messages). With `--navbot`, the tickets, cell and messages sources are calculated
from navbot's database by `server/adapters/navbot.py`.

```bash
cd dashboard
python3 run.py --navbot          # tickets, cell, messages from ../navbot/data/bot.db; everything else stays mock
```

The database path is set in `config.json` under `live.navbot.db_path` (default `../navbot/data/bot.db`, the bot's folder in
this repo). Change it if the bot runs from somewhere else.

**Status key:** ✅ calculated from navbot data · 🟡 partly (navbot has some of the inputs) · ❌ navbot has no data for it

## Skill gates and reliability (`/reliability`, Skill gates panel)

| Metric | How it's calculated | navbot source | Status |
|---|---|---|---|
| Rows (site × skill) | Latest gate result per site and skill | `SKILL_REFUSED` (gate closed), `GATE_OPEN` (gate open) | ✅ |
| Trials, successes | `run`, `passed` from that event | same | ✅ |
| Success rate | `successes / trials` (in the browser) | same | ✅ |
| Confidence P(rate ≥ 80%) | Supervisor's `confidence_pct / 100` when sent (`GATE_OPEN`). Otherwise a Beta posterior with a uniform prior: `P(p ≥ 0.8)` for Beta(passed+1, failed+1), i.e. `P(Binomial(run+1, 0.8) ≤ passed)` (`server/stats.py`) | same | ✅ |
| Gate open/closed | `open` if the latest result is `GATE_OPEN` | same | ✅ |
| Gate rule | Fixed at 80% target, 0.95 threshold (what navbot cards show) | — | ✅ |
| "N/M gates open" | Count of rows with gate `open` (in the browser) | — | ✅ |

Example from the current data: Initech `place_in_basket` 5/12 → P = 0.0012 (closed). Globex `open_middle_drawer` 13/13 → supervisor
P = 0.96 (open).

## Commissioning (`/commissioning`)

| Metric | How it's calculated | navbot source | Status |
|---|---|---|---|
| Winner policy, gate-open time | Latest `GATE_OPEN`: `policy`, posting time | `GATE_OPEN` | ✅ |
| Trial when the gate opened | `total_trials` | `GATE_OPEN` | ✅ |
| Winner's successes / trials / P | `passed`, `run`, `confidence_pct` | `GATE_OPEN` | ✅ |
| Fixed-sweep baseline | `baseline_trials` (shown in the data, not yet in the view) | `GATE_OPEN` | ✅ |
| Live chart: P per trial per policy, dropped policies | Needs every trial as it happens | navbot only gets the final result | ❌ The chart is empty; the banner and table show the winner |

## Needs (`/needs`, `/needs/T-12`, Fleet status alerts)

| Metric | How it's calculated | navbot source | Status |
|---|---|---|---|
| One Need per ticket | Grouped by `ticket_id` | `SKILL_REFUSED`, `TASK_FAILED` | ✅ |
| Title, kind, source, site, skill, deadline | From the opening event (kind `skill_unproven` or `task_failed`) | same | ✅ |
| Evidence: trials, successes, failure note, clips | `run`, `passed`, `failure_note`, `clips` | same | ✅ |
| Status | `open` → `proposed` (approval request for that ticket) → `booked` (approved) → `closed` (`GATE_OPEN` for that ticket). A new failure reopens it. Declined goes back to `open`. | `APPROVAL_REQUEST`, `APPROVAL_DECISION`, `GATE_OPEN` | ✅ |
| Assigned person | `engineer` from the latest `ETA_UPDATE` | `ETA_UPDATE` | 🟡 a name, not a Field staff ID |
| "N open · M total" | Count by status (in the browser) | — | ✅ |
| Severity P1/P2/P3 | — | Not in navbot's events | ❌ blank |
| Duration, bring list | — | Not in navbot's events | ❌ |
| Booked slot (time on the calendar) | Field calendar event with that `need_id` | Field's calendar, not navbot | ❌ |

## Need timeline (Need detail)

| Step | navbot event |
|---|---|
| Need opened | first `SKILL_REFUSED` or `TASK_FAILED` for the ticket |
| Slot proposed | `APPROVAL_REQUEST` with that `ticket_id` |
| Visit booked / declined | `APPROVAL_DECISION` (linked to the ticket through `request_id`) |
| ETA updated | `ETA_UPDATE` |
| Visit completed (gate open) | `GATE_OPEN` |
| Order shipped | `JOB_COMPLETED` with that `ticket_id` |
| Arrived, commissioning started | ❌ navbot has no events for these |

## Warehouse and robots (`/warehouse`, Robots panel, Fleet status rows)

| Metric | How it's calculated | navbot source | Status |
|---|---|---|---|
| Customer sites and their robots | Latest hourly CSV report: one robot per row, grouped by site | `robot_readings` | ✅ |
| Robot status | navbot's health check: DOWN → `blocked`, WARN → `degraded`, OK → `ok` | `robot_readings.health` | ✅ |
| Battery, temperature | `battery_pct`, `temp_c` | `robot_readings` | ✅ |
| Warehouse fleet (20 AMRs) | Latest snapshot in the latest fleet log | `fleet_snapshots` | ✅ |
| AMR status | Error → `blocked`, task `charge` → `charging`, else `ok` | `fleet_snapshots` | ✅ |
| Robots active / total | Robots not `blocked` / all robots at the site | both | ✅ |
| Site health and note | `degraded` if a robot is blocked or degraded, or the site has an open ticket | both, plus events | ✅ |
| Throughput and sparkline | `delivered` events per minute of the fleet log; total in `throughput.total` | `fleet_events` | ✅ fleet log only |
| Order backlog | — | Not in navbot's data | ❌ |
| Consumables run-out | — | Not in navbot's data | ❌ |

## Messages and ops log (`/ops`, Discord mirror)

| What | How | Status |
|---|---|---|
| `#field` | Daily summaries and recaps, approval requests, and requests people typed in `#approvals` | ✅ |
| `#cell-globex` | ETA updates | ✅ |
| `#ops-log` handoffs | One line per refusal, failure, gate open, dispatch, completion, decision, infra error and telemetry report, e.g. `T-12 opened · Globex open middle drawer refused 1/10 · engineer by 17:00`. Lines mentioning `T-…` link to the Need. | ✅ |
| `#cell-acme` | navbot doesn't post ETA updates for Acme separately | ❌ empty |

## Sources navbot can't supply

| Source | Metrics | Where they come from |
|---|---|---|
| Calendar | Staff, events, travel legs, ETAs, how late someone is (`late_by = eta − event start`), "leave in N min", change log | Field's SQLite. navbot's `DAILY_SUMMARY` only has a short list of times and places. |
| Clock | Sim time | Sim clock service |
| System | vLLM model, GPU memory, service health, cloud model calls | Local checks; set `system` to `live` |
| Feeds | Camera and robot streams | Isaac Sim, or `fixtures/clips/` |

These stay on mock (or live) when you run `--navbot`. The mock calendar still plays its scripted demo day next to navbot's real tickets.
