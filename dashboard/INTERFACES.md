# Dashboard interface assumptions: please confirm or correct

The dashboard reads six sources. Each one sits behind an adapter with a mock twin, so
changing an endpoint or a field name is a one-place fix. **Everything below is an assumption
until its owner confirms it.** Mark up this file and send it back.

- Base URLs and file paths: `config.json` → `live.*`
- Endpoint paths: `server/adapters/live.py` → `ENDPOINTS` (or override per source in `config.json` under `live.<source>.paths`)
- Field aliases already accepted are listed as `a | b`.

Conventions the dashboard assumes everywhere:

- **Times:** ISO 8601. A timestamp with no offset is treated as America/New_York; a time with an offset is converted to it.
  Bare `"HH:MM"` is also accepted and is taken to be on the demo date.
- **IDs:** strings. Need IDs look like `T-12`. Ops-log lines are scanned for `T-\d+` to link them.
- **The dashboard only reads**, except for the four demo controls (§7) and two calendar edits the user confirms
  (§2: edit event, revert change). Those go to Field, which stays the only writer to the calendar.
- **All hosts are local.** Proxy env vars are ignored, so nothing is routed off the box.

---

## 1. Ticket service (shared by Fleet and Field)  · owner: ______  · ☐ confirmed

Base URL `live.tickets.base_url` (default `http://127.0.0.1:7001`)

| Call | Returns |
|---|---|
| `GET /needs` | `[Need]` or `{"needs": [Need]}` |
| `GET /events` | `[VisitEvent]` or `{"events": [VisitEvent]}`: every visit event, all Needs |
| `POST /demo/fire` body `{"need_id": "T-12"}` | Fires the mock Need (demo control) |
| `POST /demo/reset` | Back to the start of the demo day |

**Need**

| Field | Type | Notes |
|---|---|---|
| `need_id \| id \| ticket_id` | string | `T-12` |
| `source` | string | `fleet.reliability`, `fleet.consumables`, … |
| `kind` | string | e.g. `skill_unproven`, `consumables_low` |
| `title` | string | one-line human summary (optional, falls back to `kind`) |
| `severity \| priority` | `P1`/`P2`/`P3` | |
| `site` | string | `globex`, `acme` (must match warehouse/reliability `site`) |
| `skill_req` | string \| null | robot skill, e.g. `open_middle_drawer` (must match reliability `skill`) |
| `deadline` | time | |
| `duration_min \| duration` | int minutes | |
| `bring \| bring_list` | string[] | |
| `evidence` | object | `clips: string[]` (file names under the clips root), `trials_run`, `successes`, `orders_blocked`, `policy` (all optional) |
| `status` | string | shown as-is. Mock uses `open → proposed → booked → on_site → commissioning → closed` |
| `assigned_staff \| assignee \| staff_id` | staff id | joined to Field's `staff.name` |
| `booked_slot \| slot` | `{event_id, start, end}` \| null | If missing, derived from the Field event whose `need_id` matches |
| `opened_at \| created_at`, `updated_at` | time | |

**VisitEvent**

| Field | Notes |
|---|---|
| `id \| event_id` | unique |
| `need_id \| ticket_id` | |
| `type \| event_type` | `need.opened`, `visit.proposed`, `visit.booked`, `eta.updated`, `visit.arrived`, `commissioning.started`, `visit.completed`, `order.shipped` |
| `at \| ts \| timestamp \| created_at` | time |
| `by \| actor \| agent` | `Field`, `Fleet`, or a person |
| `data \| payload` | flat object. Scalar values are shown as key/value pairs in the Need timeline |

## 2. Field SQLite (calendar)  · owner: ______  · ☐ confirmed

Path `live.calendar.sqlite_path` (default `../field/field.db`), opened **read-only**. Tables per Field spec §8;
missing tables read as empty, and extra columns pass through.

| Table | Columns used |
|---|---|
| `staff` | `id, name, skills` (JSON array or comma list)`, home_base, current_location`, optional `role` |
| `events` | `id, staff_id, title, type, start, end, place, coords` (`"lat,lon"` or JSON)`, need_id, attendance`, optional `site` |
| `legs` | `event_id, mode, minutes, leave_by, last_checked, delay_min`, optional `eta, route, lines` (JSON), `origin, status, alert, note` |
| `jobs` | `id, type, fire_at, status` |
| `changes` | `id, timestamp \| at, kind, target` (`event`/`leg`)`, event_id, staff_id, by` (`Field`/`Fleet`/`You`)`, reason, before, after` (JSON; `before` null = added, `after` null = removed)`, revertable, reverted_by`, optional `event_title, need_id` |
| `prefs` | key/value rows **or** a single row of columns |

Derived by the dashboard when absent: leg `eta = leave_by + minutes + delay_min`; leg `origin` = place of
the staff member's previous event (else `home_base`); `late_by = eta − event start`.
Event `type` values the calendar styles: `standup, pitch, lunch, dinner, visit, maintenance, office` (others render neutrally).

Field's own HTTP endpoints (demo controls only), base `live.field.base_url` (default `http://127.0.0.1:7003`):

| Call | |
|---|---|
| `POST /demo/inject-delay` body `{"line": "Red", "minutes": 12}` | Field runs its departure check against the injected alert |
| `POST /events` body `{"staff_id", "set": {"title", "type", "place", "start": "HH:MM", "end": "HH:MM"}, "reason", "by": "dashboard"}` | Add an event (from "Add change"). Field rejects overlaps with an error message the dashboard shows as-is, plans the travel legs, and logs a `changes` row `kind: "added"`, `before: null` |
| `POST /events/{id}` body `{"set": {"title"?, "place"?, "start"?: "HH:MM", "end"?: "HH:MM"}, "reason": str \| null, "by": "dashboard"}` | Edit from the dashboard (after the user confirms). Field writes it, logs a `changes` row with `by: "You"`, re-plans neighbouring legs and notifies |
| `POST /changes/{id}/revert` body `{"by": "dashboard"}` | Undo one change (Field spec: undo from the change log). Field logs a `reverted` row and sets `reverted_by` on the original |
| `POST /demo/reset` | |

`kind` values the change log knows: `booked, attached, rescheduled, delayed, replanned, edited, reverted`
(other values are shown as-is). The diff fields it formats: `start, end, leave_by, eta, delay_min, title, place, need_id`.

## 3. Fleet / cell service  · owner: ______  · ☐ confirmed

Base URL `live.cell.base_url` (default `http://127.0.0.1:7002`). Clips are served from `live.cell.clips_root`.

| Call | Returns |
|---|---|
| `GET /reliability` | `[Reliability]` or `{"reliability": [...]}` |
| `GET /commissioning` | `Commissioning` or `null` (or `{"commissioning": ...}`): the current or most recent run |
| `GET /warehouse` | `[Site]` or `{"sites": [...]}` |
| `POST /demo/reset` | |

**Reliability** (one row per site × skill). **The dashboard never computes confidence.**

`site, skill, policy, trials, successes, confidence` (P(true rate ≥ target)), `gate` (`open`/`closed`),
`threshold` (0.95), `target_rate` (0.8), `last_tested`, optional `need_id`, `note`.

**Commissioning**

```
need_id, site, skill, status ("scheduled" | "running" | "gate_open"), target_rate, gate_threshold,
started_at, gate_opened_at, gate_trial (global trial number when the gate opened), winner (policy name),
policies: [{id, name, trials, successes, confidence, status ("active" | "dropped" | "passed")}],
trials:   [{n (global trial number), policy (policy id), success (bool), at, confidence (that policy's P after this trial)}]
```

Policy order is the colour order, so keep it stable across polls.

**Site** (warehouse status)

```
site, name, area, health ("ok" | "degraded" | "commissioning" | …), health_note, need_id,
robots_active, robots_total,
robots: [{id, zone, task, battery (0-100), status ("ok" | "charging" | "blocked" | "commissioning" | …)}]   (optional; Fleet watch view),
throughput: {unit, current, series: [{t, v}]},
backlog:    {count, note, need_id},
consumables: [{item, runout (time), need_id}]
```

**Clips:** `evidence.clips` entries are file names (or relative paths) under `clips_root`. They are served at
`/media/clip/<name>` with range support. Paths that escape `clips_root` are refused. MP4 (H.264) plays everywhere.

## 3b. Camera / robot feeds (Isaac Sim, robot cameras)  · owner: ______  · ☐ confirmed

The live feed panel plays any of these per feed. The source is checked in this order:
`live.feeds.list` in config.json (a static list), then `live.feeds.url` (returns `[Feed]` or
`{"feeds": [...]}`), then the cell service's `GET /feeds`.

**Feed**: `id`, `label`, `site` (matches warehouse `site`), `robot_id` (matches a warehouse robot `id`;
omit for a site overview camera), `kind`, `url`, optional `refresh_ms` (for `image`), optional `proxy: true`.

| `kind` | What the browser does | Typical source on the box |
|---|---|---|
| `mjpeg` | `<img src=url>` (multipart MJPEG stream) | ROS 2 `web_video_server` on an Isaac Sim camera topic, e.g. `http://127.0.0.1:8081/stream?topic=/globex/cam/image_raw` (default port 8080 clashes with OTP) |
| `image` | Re-fetches a JPEG/PNG every `refresh_ms` | A sim script writing frames to an HTTP endpoint |
| `video` | `<video>` (MP4/WebM, autoplay, muted, looped) | Recorded LIBERO/Isaac Sim clips |
| `iframe` | Embeds a page | Isaac Sim WebRTC web client, e.g. `http://127.0.0.1:8211/streaming/webrtc-client?server=127.0.0.1` |
| `synthetic` | Draws the cell from robot telemetry | Mock mode only |

`proxy: true` makes the dashboard relay the stream at `/media/feed/<id>`, so the browser only talks to the
dashboard. Use it when the stream binds to localhost on another machine or port. HLS is not supported
(it would need a bundled player). Robot status, battery and task on the feed come from §3 `robots[]`,
not from the stream.

## 4. Sim clock service  · owner: ______  · ☐ confirmed

Base URL `live.clock.base_url` (default `http://127.0.0.1:7000`)

| Call | |
|---|---|
| `GET /clock` | `{"sim_time": ISO, "speed": 60}` (also accepts `now \| time \| iso`) |
| `POST /clock/set` body `{"time": "13:25"}` | Same as `/clock set 13:25` in Discord |
| `POST /clock/speed` body `{"speed": 60}` | |
| `POST /clock/reset` | Optional. If it's missing, reset sets the time to `demo.start_time` and speed to 1 |

The dashboard reads the clock every ~0.5 s and interpolates in between using `speed`.

## 5. Message log  · owner: ______  · ☐ confirmed

The bots append one JSON object per line to `live.messages.jsonl_path` (default `../shared/messages.jsonl`);
the dashboard reads the last 512 KB. Alternatively set `live.messages.url` to an endpoint returning
`[Message]` or `{"messages": [...]}`.

`channel` (`field`, `cell-globex`, `cell-acme`, `ops-log`; a leading `#` is fine), `author | user | from`, `text | content`,
`at | ts | timestamp`, optional `id | message_id`, optional `bot` (defaults to true for authors `Field`/`Fleet`).

**The Ops log view is the `ops-log` channel**, one line per handoff, e.g.
`T-12 booked · Jordan · 3:00–3:45 Globex`.

## 6. System  · owner: ______  · ☐ confirmed

- vLLM: `GET {vllm_url}/v1/models` → first model id is shown (expected `Qwen3.6-35B-A3B NVFP4`).
- GPU memory: `nvidia-smi --query-gpu=memory.used,memory.total`. GB10 memory is unified, so if that reports N/A
  the dashboard shows used system memory from `/proc/meminfo`, labelled as such.
- Service health: TCP connect to each `live.system.services[]` host:port (vLLM 8000, GR00T 5555/5556, OTP 8080, ticket 7001, clock 7000).
- **Cloud model calls:** `egress_counter_url` (`{"cloud_model_calls": 0}`) or `egress_counter_file` (a plain integer or
  that JSON). **Until one is wired, the dashboard shows "not wired" instead of a fake 0.** Who owns the egress counter? ______

## 7. Demo controls (`/control`)

| Control | Calls |
|---|---|
| Set clock / speed | clock `POST /clock/set`, `POST /clock/speed` |
| Inject MBTA delay | Field `POST /demo/inject-delay` |
| Fire mock T-12 | tickets `POST /demo/fire` |
| Reset demo day | `POST /demo/reset` on tickets, Field, cell; clock reset |

## Open questions

0. Isaac Sim pair: which camera streams exist (per cell, per robot), what kind (MJPEG via web_video_server,
   WebRTC client, recorded clips), and on which ports?
1. Robot pair: is reliability/trial data served over HTTP by the cell service, or written to a file/SQLite we should read instead?
2. Travel pair: final ticket format: does it match the Need/VisitEvent fields above? Who serves `GET /events`?
3. Where do failure clips land on disk, and in what format?
4. Do the bots already log Discord messages somewhere we can read, or should they append to `messages.jsonl`?
