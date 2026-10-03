# navbot: APIs and services

navbot is the Fleet & Field Discord bot. This page lists every API and service it talks to.

**MCP servers: none.** navbot doesn't use any MCP servers. It only uses the HTTP APIs below.

## Overview

```
 Supervisor bot ──POST /events──▶  navbot  ──Discord API──▶ Discord channels
        ▲                            │  │
        │                            │  └──OpenAI-compatible API──▶ Qwen (llama.cpp, local)
        └──POST /decisions───────────┘
                                     └──▶ data/bot.db (SQLite)
```

| # | API / service | Direction | Address | Auth |
|---|---|---|---|---|
| 1 | Discord API | navbot → Discord | `discord.com` (Gateway + REST) | `DISCORD_TOKEN` |
| 2 | navbot events API | supervisor → navbot | `http://127.0.0.1:8787` | `X-Bot-Key` header |
| 3 | Supervisor decisions webhook | navbot → supervisor | `SUPERVISOR_DECISION_URL` | `X-Bot-Key` header |
| 4 | Local LLM (Qwen) | navbot → model | `http://127.0.0.1:8080/v1` | none (`LLM_API_KEY` if set) |
| 5 | SQLite database | navbot → file | `data/bot.db` | file access |

The only external service is Discord. Everything else runs on the same machine.

## 1. Discord API

navbot uses the **discord.py 2.x** library, which handles the Discord API.

- **Gateway (WebSocket):** used to log in and receive reaction events in `#approvals`.
- **REST:** used to post messages, upload clips, add and remove reactions, and edit approval posts.
- **Intents:** default plus **Message Content** (privileged). Turn it on in the Developer Portal under Bot → Privileged Gateway Intents; navbot needs it to read requests typed in `#approvals`. If it's off, navbot logs an error and runs without it, and only supervisor approvals work.
- **Bot permissions:** View Channels, Send Messages, Embed Links, Attach Files, Read Message History, Add Reactions, Manage Messages.
- **Settings:** `DISCORD_TOKEN`, the six `CH_*` channel IDs, `ROLE_APPROVER`, plus optional `ROLE_FIELD_ENGINEER` and `ROLE_CUSTOMER`.

## 2. navbot events API (inbound)

navbot runs a small HTTP server (aiohttp) that the supervisor bot calls.

| Method | Path | What it does |
|---|---|---|
| `POST` | `/events` | Takes one JSON event and posts it to the right channel |
| `POST` | `/telemetry` | Takes the hourly robot telemetry (CSV or JSON fleet log) and posts a health card to `#monitor-bot` |
| `GET` | `/health` | Returns `{"ok": true, "discord_ready": true}` once logged in |

- **Header:** `X-Bot-Key: <BOT_SHARED_KEY>`
- **Event types:** `SKILL_REFUSED`, `TASK_FAILED`, `INFRA_ERROR`, `GATE_OPEN`, `JOB_DISPATCHED`, `JOB_COMPLETED`, `DAILY_SUMMARY`, `DAILY_RECAP`, `APPROVAL_REQUEST`, `ETA_UPDATE`. Section 3 of the bot spec lists the fields for each.
- **Responses:**

  | Code | Meaning |
  |---|---|
  | `200` | Posted |
  | `400` | Bad or missing field |
  | `401` | Wrong key |
  | `409` | Duplicate approval `request_id` |
  | `500` | Channel not found |
  | `502` | Discord rejected the post |

- **Settings:** `LISTEN_HOST`, `LISTEN_PORT`, `BOT_SHARED_KEY`. If the supervisor runs in a container, set `LISTEN_HOST=0.0.0.0`.

Example:

```bash
curl -X POST http://127.0.0.1:8787/events \
  -H 'X-Bot-Key: change-me' -H 'Content-Type: application/json' \
  -d '{"event":"INFRA_ERROR","source":"gr00t-5556","message":"policy server not responding"}'
```

### Hourly telemetry (`POST /telemetry`)

The supervisor sends one CSV per hour, with one row per robot. navbot posts a health table to `#monitor-bot`. No file is attached; the raw CSV is saved in `data/bot.db`.

- **Body:** raw CSV, `Content-Type: text/csv`, `X-Bot-Key` header.
- **Columns navbot reads:** `robot` (required), `site`, `status`, `in_use`, `battery_pct`, `temp_c`, `errors_1h`, `last_seen`. Other columns are kept in the raw CSV saved in `data/bot.db`.
- **In use:** if an `in_use` column is present, only rows with `1`, `yes` or `true` are shown.
- **Health:**

  | Health | Rule |
  |---|---|
  | 🔴 DOWN | status is offline, error, fault, e-stop or disconnected |
  | 🟡 WARN | status is degraded, charging or paused; battery under 20%; temp 70 °C or more; or any errors in the last hour |
  | 🟢 OK | everything else |

- **Late reports:** if no CSV arrives for `TELEMETRY_STALE_MIN` minutes (default 75), navbot posts a warning in `#monitor-bot`.
- **Settings:** `CH_MONITOR_BOT`, `TELEMETRY_STALE_MIN`.

```bash
curl -X POST http://127.0.0.1:8787/telemetry -H 'X-Bot-Key: change-me' -H 'Content-Type: text/csv' \
  --data-binary $'robot,site,status,in_use,battery_pct,temp_c,errors_1h,last_seen\nGR1-07,Globex,running,1,82,41,0,13:58\n'
```

#### JSON fleet log (FleetOps / Isaac Sim)

`/telemetry` also accepts the FleetOps fleet log as JSON. navbot detects it because the body starts with `{`. Its format is in `samples/fleet_log_5min.json`:

| Part | What it holds |
|---|---|
| `meta` | Error codes, event types and layout |
| `summary` | Totals per robot and per zone |
| `events` | `t` is seconds since the run started |
| `snapshots` | Every robot every 5 s |

- **Robot names:** robots are shown as `AMR-00` … `AMR-19`, from the numeric `id`.
- **Times:** taken from `meta.generated_at` minus `sim_seconds`, plus `t`. If `generated_at` is missing, the card shows `t+m:ss` instead.
- **The card:**
  - **Status table:** every robot at the latest snapshot (zone, task, moving/waiting, battery, temp).
  - **Needs attention now:** robots with a problem at the latest snapshot.
  - **What happened:** each fault with its error code and how long it took to clear, marked when it was a scripted/presenter demo fault. Traffic jams are listed too: 3 or more robots released together after waiting 10 s or longer.
  - **Throughput:** picks, deliveries and charges.
  - **Congestion:** traffic waits and how often the pack stations were full.
  - **Stock mismatches:** bins the system counted as stocked but the robot found empty.
- **No attachment:** the post is the card only. Every snapshot row is saved in the `fleet_snapshots` table instead.
- **Health:**

  | Health | Rule |
  |---|---|
  | 🔴 DOWN | `error` is `DRIVE_FAULT` (can't move) or `OVERHEAT` (motor above 80 °C) |
  | 🟡 WARN | `error` is `LOW_BATTERY` or `BIN_EMPTY`; battery under 25% and not charging; temp 78 °C or more; or stuck in traffic 20 s or longer |
  | 🟢 OK | everything else |

  Thresholds are at the top of `fleetlog.py`.
- **For testing:** `python mock_supervisor.py fleetlog [file]` sends `samples/fleet_log_5min.json` by default.

## 3. Supervisor decisions webhook (outbound)

Every approval card shows its options as reactions, which the bot adds itself. When one option's count reaches **2** (the bot's own reaction plus one approver), that option is the decision. Reactions from non-approvers, on other emoji, or on a request that's already decided are removed. The card's Status field changes from ⏳ Pending to ✅ Approved or ❌ Denied, with who decided and when.

If the supervisor sent the request, navbot sends it the decision. If a person posted the request in `#approvals`, navbot replies to that person instead.

- **Call:** `POST` to `SUPERVISOR_DECISION_URL`, with the `X-Bot-Key` header.
- **Retries:** 3 tries. If all fail, the approval post turns red and `#ops-log` gets a warning.
- **Body:**

  ```json
  { "event": "APPROVAL_DECISION", "request_id": "apr-book-1727970000",
    "choice": "book", "status": "approved", "approved_by": "Megha", "approved_by_id": "1234567890",
    "time": "2026-10-03T15:02:11-04:00" }
  ```

- **For testing:** `python mock_supervisor.py listen` stands in for the supervisor on `http://127.0.0.1:8788/decisions`.

## 4. Local LLM: Qwen (OpenAI-compatible API)

Qwen writes the optional one-line headline above each post. Inference stays on the machine.

- **Server:** llama.cpp (`llama-server`) on port 8080, model `qwen3.5-4b`.
- **Endpoint:** `POST {LLM_URL}/chat/completions`, using the OpenAI chat format.
- **Thinking:** turned off with `chat_template_kwargs: {"enable_thinking": false}`.
- **Number check:** code drops any headline that contains a number not in the event. If the model is down, slow or wrong, the post goes out without a headline.
- **Settings:**

  | Setting | Value |
  |---|---|
  | `LLM_ENABLED` | `1` (set `0` to turn headlines off) |
  | `LLM_URL` | `http://127.0.0.1:8080/v1` |
  | `LLM_MODEL` | `qwen3.5-4b` |
  | `LLM_TIMEOUT` | `20` |
  | `LLM_API_KEY` | empty |

- **To see which model is served:** `curl http://127.0.0.1:8080/v1/models`, then copy the `id`.

The spec expects Qwen3.6-35B-A3B on vLLM. navbot works with either: change `LLM_URL` and `LLM_MODEL` to switch.

## 5. SQLite database

This isn't a network API. The dashboard reads the same file.

- **File:** `data/bot.db`, set with `DB_PATH`. It uses WAL mode so the dashboard can read while navbot writes.
- **Tables:**
  - `activity`: everything that happens through the bot, one row each (`at`, `kind`, `channel`, `user_name`, `message_id`, `request_id`, `detail` JSON). Kinds include `event_received`, `event_rejected`, `card_posted`, `message`, `request_created`, `reaction_added`, `reaction_removed`, `reaction_rejected`, `reaction_counted`, `request_decided`, `decision_delivered`, `decision_not_delivered`, `requester_notified`, `telemetry_posted`, `telemetry_rejected`, `telemetry_late`, `ops_log_posted`, `bot_ready`.
  - `approvals`: one record per request, updated in place as it is decided. Fields: `request_id`, `source` (`supervisor` or `discord`), `kind`, `summary`, `details`, `requested_by`, `requested_at`, `status` (`pending`, `approved`, `denied`, or `decided` for options other than ✅/❌), `choice_label`, `decided_by_name`, `decided_at`, `delivered`, `updated_at`.
  - `telemetry_reports`: one row per robot report received (`received_at`, `robots`, `ok`, `warn`, `down`, raw `csv`).
  - `robot_readings`: the robot monitor log. One row per robot per report, appended each time (`robot`, `site`, `status`, `battery_pct`, `temp_c`, `errors_1h`, `last_seen`, `health`, `reasons`, `report_id`).
  - `fleet_events`: every event from a JSON fleet log, appended per report (`report_id`, `t`, `at`, `type`, `robot`, `job_id`, `error`, full `payload`).
  - `fleet_snapshots`: every robot at every snapshot of a JSON fleet log (`report_id`, `t`, `at`, `robot`, `zone`, `x`, `y`, `vel`, `task`, `job_id`, `battery`, `temp`, `error`, `carrying`, `yielding`, `waiting_s`). For a fleet log, `telemetry_reports.csv` holds `meta` + `summary` as JSON.
  - `events`: every card posted and decision sent (`event`, `payload`, `message_id`, `posted_at`).

- **Requests posted in `#approvals`:** any new message (not a reply) from a person becomes a request. navbot works out the type from keywords (`TIME_OFF`, `SCHEDULE_CHANGE`, `SITE_VISIT`, `TRAVEL`, `PURCHASE`, `ACCESS`, or `OTHER`; see `approvals.py`), gives it an ID such as `REQ-0001`, and replies with a request card that has ✅ Approve and ❌ Deny.

- **Useful queries:**

  ```sql
  SELECT request_id, kind, requested_by, status, decided_by_name, datetime(decided_at,'unixepoch','localtime') FROM approvals ORDER BY requested_at DESC;
  SELECT datetime(received_at,'unixepoch','localtime'), robot, health, battery_pct, temp_c, reasons FROM robot_readings WHERE robot='GR1-03' ORDER BY received_at;
  SELECT datetime(at,'unixepoch','localtime'), kind, channel, user_name, request_id, detail FROM activity ORDER BY id DESC LIMIT 50;
  ```

## Python packages

From `requirements.txt`:

| Package | Used for |
|---|---|
| `discord.py>=2.4,<3` | Discord API |
| `aiohttp>=3.9` | Events API server, decisions webhook, LLM calls |
| `python-dotenv>=1.0` | Reads `.env` |
| `tzdata` | America/New_York times in audit lines |
