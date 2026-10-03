# Fleet & Field dashboard

A command-center dashboard for the recorded demo. It covers fleet surveillance (live camera/robot feeds,
robot status, skill gates, alerts) and the field scheduler side by side. It runs entirely on the GB10: Python 3.9+ standard library
on the server, plain JS/CSS in the browser. There is nothing to install, no CDN, no remote fonts, and no LLM calls.

## Start (one command)

```bash
python3 run.py
```

Open http://localhost:8090. The demo controls are at http://localhost:8090/control.

## Mock vs live

`config.json` → `"mode": "mock"` or `"live"`. You can also override it at startup without editing the file:

```bash
python3 run.py --mode live        # or: DASH_MODE=live python3 run.py
```

To go live one source at a time, set that source in `"sources"`, e.g. `"tickets": "live"` with everything
else `null` (follows `mode`). The header shows **MOCK DATA** while any source is mocked, and `/proof` lists
each source's mode and health. A source that goes down keeps its last good data and shows an
"offline" badge, so a recording never goes blank.

Live endpoints, paths and field names are in **[INTERFACES.md](INTERFACES.md)**, the file to hand to the team.

### Fleet data from the Discord bot (`--navbot`)

```bash
python3 run.py --navbot
```

This reads Needs, skill gates, commissioning, warehouse/robots and the ops log from navbot's database
(`live.navbot.db_path`, default `../navbot/data/bot.db`), read-only. Calendar, clock, system and feeds keep
following `mode`. **[METRICS.md](METRICS.md)** lists every metric, how it's calculated, and which navbot data feeds it.

## Views (each has its own URL; keys work while recording)

**Home (`/`) is the command center**, everything on one screen:

- **Live feed** (biggest panel): one camera or robot stream with a HUD (robot status, task, battery, and the
  commissioning trial when one is running). Switch with the site tabs, the robot chips, `[` / `]`, or by
  clicking any robot or "Watch" button anywhere on the dashboard.
- **Fleet status**: alerts (open Needs, blocked robots) and one row per site.
- **Robots**: every robot by site; click to watch it.
- **Skill gates**: the live commissioning chart and every site × skill gate.
- **Scheduler** on the side: the viewer's day in compact form, with *Scheduler only*, *Add change*, and *My day*.
- **Ops log**: cross-agent handoffs.

Every panel's **Open ›** link goes to its deep dive. Panels on the command center, `/live`, and `/ops` can be
**minimized** (−) down to their header, which keeps the key numbers, and the remaining panels grow to fill
the space. A column whose panels are all minimized folds into a slim strip. **Focus** (⤢) minimizes
everything else; press it again to restore. The layout is remembered per page in this browser. The left rail is grouped as surveillance, then scheduler,
then ops & proof, followed by the staff avatars (click one to view as that person) and Settings.

| Key | URL | View |
|---|---|---|
| 1 | `/` | **Command center** |
| l | `/live` | **Live feeds**, full size, with the robot list |
| m | `/plan` | **Scheduler only**: just the plan, plus *Add change* and a menu (Dashboard, Full day view, switch person) |
| d | `/day` | **My day**: the viewer's own plan, the simplest layout (Plan list or Timeline), travel between events, a "now" marker. **Updates** button (top right) lists plan changes this person hasn't seen yet |
| 2 | `/calendar` | Team calendar, all staff |
| 3 | `/needs` | Needs feed |
| f | `/fleet` | **Fleet watch**: what Fleet is surveying (sites, robots, skill gates, commissioning) and which signals made it into the plan |
| u | `/fleet-updates` | **Fleet updates**: everything Fleet posted or did, newest first |
| h | `/changes` | **Changes**: full change log (bookings, MBTA delays, reschedules, edits) with **Edit** and **Revert** |
| 9 | `/ops` | Ops log + Discord mirror |
| p | `/proof` | Local proof: vLLM, model, GPU memory, services, cloud model calls |
| s | `/settings` | Appearance (theme, accent, density, 12/24h, highlight, default plan layout) and Account |
| 4 | `/need`, `/needs/T-12` | Need detail: clips, trial stats, calendar block, timeline |
| 5 6 7 8 0 | `/reliability` `/commissioning` `/warehouse` `/leg` `/discord` | Single views full screen |
| c | `/control` | Demo control panel (not in the rail) |

Open a drawer straight from the URL: `/plan?add=1` (Add change), `/plan?menu=1` (open the menu), `/?event=E4`
(event details + edit), `/day?updates=1` (updates list), `/?user=s2` (view as Sam). Esc closes a drawer.

The sim clock and a one-line local-proof strip are on every page. Anything that just appeared or changed
(a booked visit, a status change, a new log line) is briefly highlighted.

**Edits:** clicking an event (My day, search, or Edit in the change log) opens its details with an edit form.
Saving or reverting asks for confirmation, then goes to **Field** (`POST /events/{id}`,
`POST /changes/{id}/revert`). Field stays the only writer to the calendar, re-checks travel and notifies
people. In mock mode the mock applies the change itself. The person you're viewing as, settings, and
"seen" markers are kept per browser in localStorage.

## Mock demo day (what plays when)

In mock mode the whole day is scripted on the sim clock (`fixtures/script.json`). State is rebuilt from
scratch for any clock time, so jumping the clock back and forth is safe.

| Sim time | Scene |
|---|---|
| 08:05 | T-11 attached to the Acme pitch (change log: "Need attached") |
| 08:45 | 1 Morning brief in #field |
| 11:50 | Client dinner rescheduled 7:00 → 7:30 PM; leave-by moves to 7:04 (change log: "Rescheduled") |
| 09:12 | 2 "how many pitches today?" → 3 |
| 12:40 | 3 Fleet opens **T-12** (P1, Globex open_middle_drawer 1/10, 40 drawer orders held) |
| 12:41–12:43 | 4 Field proposes 3:00–3:45, Jordan taps ✅, the **V1** block appears on the calendar |
| 13:25 | 5 Departure check: Red Line +12 → "leave now or taxi"; 13:27 choice applied, ETA 2:07 PM in #cell-globex |
| 15:00 | 6 Arrived → commissioning from 15:02; bad policy dropped at its 8th trial; **gate opens at trial 29 (~15:26)** |
| 15:29 / 15:32 | T-12 closed, order G-4471 shipped, backlog drains |
| 18:00 | 7 Recap in #field |

**Fire T-12** and **Inject delay** on `/control` move those beats to "now". With `"auto": false` in
`script.json`, those beats only happen when fired from the panel. **Reset** puts the clock back to 08:30 at ×1.

Recording tip: set the clock to 2–3 minutes before a scene at ×60, then let it run.

## Styling and layout (Amal)

- `web/theme.css` is the **only** place for colours, type, spacing and highlight timing (CSS variables).
  Dark is the default (gradient day-plan card, a colour per event type). The `[data-theme="light"]` and
  `[data-density="compact"]` blocks are what Settings switches to.
- `web/base.css` holds structure only and reads the variables.
- `web/layout.js` sets routes, nav order, which views share a page, and keyboard keys. Page grids are
  `.layout-<name>` classes in `base.css`.
- Each view in `web/views/*.js` returns plain HTML with stable class names. Restyle freely without touching logic.

## Files

```
run.py                 one-command start
config.json            mode flag, ports, live endpoints and paths
INTERFACES.md          every assumed endpoint and field (hand to the team)
fixtures/              demo day: calendar, needs, cell/warehouse, mock story script
server/app.py          HTTP server, demo controls, clip streaming
server/snapshot.py     reads all adapters in the background, normalises, derives links
server/adapters/       one adapter per source: live.py, mock.py (+ mock_world.py)
web/                   index.html, app.js (shell/router/poller), state.js (viewer, settings, seen, drawer),
                       drawers.js (event edit, updates), feeds.js (feed switching, simulated camera),
                       icons.js, theme.css, base.css, layout.js, views/
```

## Live feeds (Isaac Sim / robot cameras)

Mock mode draws a **simulated camera** for each site and robot, from Fleet's robot telemetry, labelled
SIMULATED on screen. To use real video before the streams exist, drop a recording at
`fixtures/clips/<feed id>.mp4` (e.g. `cam-globex.mp4`, `G-03.mp4`) and it plays instead.

To connect the real streams, set `"feeds": "live"` under `sources` (or the global mode) and list them in
`config.json`:

```json
"feeds": { "list": [
  { "id": "cam-globex", "label": "Globex cell overview", "site": "globex", "kind": "iframe",
    "url": "http://127.0.0.1:8211/streaming/webrtc-client?server=127.0.0.1" },
  { "id": "G-03", "label": "G-03 wrist cam", "site": "globex", "robot_id": "G-03", "kind": "mjpeg",
    "url": "http://127.0.0.1:8081/stream?topic=/globex/g03/image_raw", "proxy": true }
] }
```

Supported kinds: `mjpeg`, `image` (polled snapshot), `video`, `iframe` (e.g. Isaac Sim's WebRTC web
client), `synthetic`. Or have the cell service serve `GET /feeds`. See INTERFACES.md §3b.

## Clips

Clips: put MP4s under `live.cell.clips_root` and list their file names in a Need's `evidence.clips`.
No clips ship with the mock, so the Need page shows a "clip not found" placeholder until you drop the
MP4s named in `fixtures/needs.json` into `fixtures/clips/`.
