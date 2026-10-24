# Hyperion x Fo: build plan (v2)

**Event:** Wajo x Anthropic "Get Sh*t Done" hackathon. **Team:** Megha, Ferbin, Thiago.
**Deadlines:** X quote-repost **Mon Oct 19, 10:15 AM PT = 1:15 PM ET**. Discord form **Tue Oct 20, 10:15 AM PT = 1:15 PM ET**.
**All times below are ET.**

---

## 0. The pitch in one sentence
> **When a warehouse robot breaks, Claude gets a real human technician on the way through Fo (phone call, calendar, email), the fleet re-plans around the repair, and code makes sure the agent can only ever contact the people it's allowed to.**

The robots are simulated (Isaac Sim, 20 Nova Carters). The call, calendar invite and emails are real. We say exactly that, everywhere.

---

## 1. Verified facts this plan stands on (checked 10-18)
| Fact | Status |
|---|---|
| Fo MCP server `https://mcp.api.wajo.ai/fo` is connected in Claude Code on Ferbin's laptop | ✔ `claude mcp list` says Connected |
| Fo tools: `ask_fo`, `get_fo_reply` (polls, 40 s waits), `list_fo_conversations`, `get_fo_conversation`, `start_fo_call` | ✔ from the MCP tool list |
| Fo can: outbound phone calls (one at a time, with AI disclosure + recorded-line notice, transcript + summary), Gmail, Calendar, Docs/Sheets, purchases with confirm-first | ✔ Fo's own answers, 10-17 (FO_USE_CASE.md) |
| Fo can't: webhooks, inbound callbacks, Slack on our account | ✔ same source. So **we drive Fo and poll it**; replies come in the call transcript or by email |
| Hyperion live bridge: `sim_bridge.py` (:3001) takes fleet pushes and hands back commands; `warehouse_live.py --bridge` (Isaac GUI) and `fleet_runner.py` (headless) both push the same `fleet.py` | ✔ code read 10-18 |
| Agents exist: Surveillance, WarRoom (code-enforced `request_technician`), Scheduler (technicians are timers today), Allocator (Hungarian re-homing) | ✔ `~/hyperion-grok/agents.py` |
| Bridge only queues `inject/clear/shrink` today; `reassign/audit/clear-one-robot` exist only in hyperion-grok's `fleet.py` | ✔ **to fix (task B2)** |
| Claude Agent SDK not installed; `claude` CLI 2.1.303 is | ✔ **use `claude -p --output-format stream-json`** (gives the tool-call trace for form field F for free) |

---

## 2. Architecture

```
 ┌───────────────────────── Ferbin's laptop ──────────────────────────────────────────┐
 │                                                                                     │
 │  Isaac Sim GUI (warehouse_live.py --bridge)   ── or fallback ──  fleet_runner.py    │
 │        │ push telemetry+events @8 Hz, receive commands                             │
 │        ▼                                                                            │
 │  sim_bridge.py :3001   (/api/telemetry, /api/events, /api/command  ← new generic)   │
 │        ▲                         │                                                  │
 │        │ commands                │ telemetry (1 Hz poll)                            │
 │        │                         ▼                                                  │
 │  ops_live.py  (NEW: the live agent loop)                                            │
 │    Surveillance → WarRoom → Scheduler → Allocator    (existing agents, now on Claude)│
 │                     │ request_technician (code-enforced)                            │
 │                     ▼                                                               │
 │    Dispatcher (NEW) ── policy.py gate ──► `claude -p` (Fo tools ONLY)               │
 │         ▲                                     │ PreToolUse hook = policy check      │
 │         │ ETA, status, transcript             ▼                                     │
 │         └────────────────────────────── Fo (Wajo cloud) ── real phone / Gmail / Cal │
 │                                                                                     │
 │  trace.jsonl  ──►  panel.html :8300 (NEW: live incident + Fo timeline)              │
 └─────────────────────────────────────────────────────────────────────────────────────┘
```

**Design rule (already Hyperion's): code enforces, the model explains.** The model writes briefs and reads transcripts. Code decides who may be contacted, how often, and what counts as a valid ETA.

---

## 3. Components, owners, acceptance tests

### A. Fo smoke tests (owner **Megha**, start immediately) — gates everything
Run from Claude Code on the laptop (or Megha's own Fo account). Log every result in `runs/smoke.md`.
| # | Test | Pass when | Record |
|---|---|---|---|
| A1 | `ask_fo`: "Email <Megha> a test: subject HYPERION SMOKE 1" | email arrives | latency ask→delivered |
| A2 | `ask_fo`: "Put a 30-min event 'Hyperion smoke' on <calendar> tomorrow 9 AM, invite <Thiago>" | invite arrives | latency |
| A3 | `ask_fo`: "Call <Thiago's phone>. Say robot 7 in aisle C overheated and ask when he can be on site. Report his answer." | phone rings, Fo reports the ETA in its reply | **ask→ring, ring→summary**, transcript format |
| A4 | Same as A3 but Thiago doesn't answer | Fo reports "no answer" cleanly | how failure looks |
| A5 | Does Fo ask for confirmation before calling/emailing? | note the exact question text | needed for the policy auto-answer |
**Gate G1 (9:30 PM):** A1 + A3 pass → full plan. A3 fails → **Plan B: email dispatch** (Fo emails the tech, the tech replies with an ETA, Fo reads the reply). Still real, same demo shape.

### B. Live agent loop (owner **Ferbin**)
New folder `~/hyperion-fo` (shareable). Copy in `fleet.py, agents.py, llm.py, pusher.py` (hyperion-grok versions), `sim_bridge.py, fleet_runner.py, warehouse_live.py`.
| # | Task | Acceptance test |
|---|---|---|
| B1 | `llm.py`: add `anthropic` backend (`claude-sonnet-5-5`, event credits key in `.env`), keep local fallback; log backend on every line | `python3 -c` one chat call returns, log says `anthropic` |
| B2 | `sim_bridge.py`: add `POST /api/command` passing any fleet command (`reassign`, `audit`, `clear` with `robot`) | curl a `reassign`, `fleet_runner` prints it applied |
| B3 | `ops_live.py`: poll `/api/telemetry` 1 Hz; run the four agents on wall-clock seconds; send commands to `/api/command`; write `runs/<id>/trace.jsonl` | inject an OVERHEAT via `/api/inject` → trace shows surveillance → war_room `request_technician` within 6 s |
| B4 | Scheduler change: a technician ticket waits for `dispatch.eta_min` instead of a fixed timer; demo clock `ETA_SCALE` (default 1 real min = 2 sim s, shown on screen) | with a fake ETA of 10 min, the robot clears 20 s later |
| B5 | Isaac GUI with `--bridge` runs the same loop (Ferbin launches it himself) | the robot visibly stops, others re-route, it resumes after the clear |

### C. Dispatcher + policy (owner **Megha**, with Ferbin on the wiring)
| # | Task | Acceptance test |
|---|---|---|
| C1 | `contacts.yaml`: role → name, phone, email, consent note (each teammate writes "I consent to Fo calling/emailing me for the Hyperion demo" in team chat; paste the time) | file reviewed by all 3 |
| C2 | `policy.py`: allowlist; **max 1 call + 3 emails + 1 calendar event per incident; max 3 incidents per run**; quiet hours off for the demo window only; words like buy/order/purchase/pay blocked unless `--allow-spend`; `STOP` file = kill switch | unit tests: an off-list number is blocked, a 2nd call is blocked, a purchase is blocked |
| C3 | `dispatch.py`: builds the brief (Claude writes 2 sentences: fault, likely cause, what to bring), then runs `claude -p` with `--allowedTools mcp__fo__ask_fo,mcp__fo__get_fo_reply` only, `--output-format stream-json`, and a **PreToolUse hook** that re-checks every `ask_fo` message against `policy.py` (blocks if it names any phone/email not on the allowlist) | dry run prints the exact Fo instructions; a forced bad recipient is blocked by the hook and logged |
| C4 | Steps the dispatcher asks Fo to do, in order: (1) call the on-call tech, read the brief, ask for an ETA; (2) calendar block "Robot 7 repair, aisle C" at now+ETA for the tech; (3) email the incident report to the ops lead. Later, on clear: (4) closing email with the downtime | in the live run all 4 happen and each Fo reply is in the trace |
| C5 | ETA parsing: Claude extracts `{answered, eta_min, notes}` as JSON from Fo's call summary; code accepts 5 ≤ eta ≤ 120, else uses the 15-min default and logs `fallback_used` | test against the A3/A4 transcripts |
| C6 | If Fo asks a confirmation question, auto-answer **only** the ones in policy (e.g. "yes, call Thiago, he consented"); anything else pauses and shows a **human approve button** in the panel | simulate with the A5 question text |

### D. Trace, panel, run counter (owner **Thiago**)
| # | Task | Acceptance test |
|---|---|---|
| D1 | `trace.jsonl` schema: `{t_wall, t_sim, agent, kind, incident, payload}`; Fo tool calls copied from the stream-json (tool name, input summary, status, latency) | one run file reads cleanly |
| D2 | `panel.html` (served by ops_live on :8300, polls `/trace` 1 Hz): left = fleet stats (deliveries, waiting robots); right = incident timeline: DETECTED → DIAGNOSED → POLICY OK → FO CALLING → ETA 15 min → CALENDAR ✔ → EMAIL ✔ → TECH ON SITE → CLEARED → CLOSING EMAIL ✔ | readable at 1080p next to the Isaac view |
| D3 | `runs/summary.csv`: run id, start, success (y/n), failed step, Fo latency per step | form fields F + G come straight from it |
| D4 | `replay_check.py` on the new runs (the fleet part stays deterministic given the command log) | REPLAY MATCHES on 1 run |

**Success definition (one run):** the fault is detected, Fo's call completes with an ETA (or the fallback is logged), the calendar and email are confirmed by Fo, the robot is cleared, and the closing email is confirmed, all within 6 minutes and with zero policy violations.

---

## 4. Timeline with go/no-go gates
| ET | Megha | Ferbin | Thiago | Gate |
|---|---|---|---|---|
| 8:00-9:30 PM | A1-A5 Fo smoke tests; consent messages | B1, B2, folder | D1 schema, D2 panel skeleton | **G1 9:30:** Fo call works? (else Plan B email) |
| 9:30-11:00 | C1-C3 policy + dispatcher (dry run) | B3, B4 ops_live headless with fleet_runner | D2 wired to trace | **G2 11:00:** dry-run loop end to end (Fo calls printed, not sent) |
| 11:00 PM-12:30 AM | C4-C6 live | integrate, fix | D3 counter; plays the tech on the phone | **G3 12:30:** 1 full LIVE run succeeds |
| 12:30-1:30 AM | 2-3 more live runs, fix flakes, freeze code (`git tag demo-freeze`) | | | **G4 1:30:** ≥3 successes → sleep |
| 8:00-9:00 AM | rehearse | B5 Isaac GUI + bridge check; OBS scene | phone on camera | **G5 9:00:** Isaac live OK? (else headless + pre-rendered Isaac of the same run, said on screen) |
| 9:00-10:30 | record the one-take (2-3 attempts) | record | tech on camera | |
| 10:30 AM-12:30 PM | write-up + build note (drafts below) | cut 60-90 s X version from the take | run count, trace export | |
| **1:00-1:15 PM** | | **quote-repost Shivani's 10 AM PT post** | like + share | **deadline 1:15** |
| Mon evening / Tue AM | Discord form (fields A-G) | | | **Tue 1:15 PM** |

Rule: if a gate fails, take the fallback immediately; never trade sleep for a stretch goal.

---

## 5. Demo: the one unedited take (~3 min), screen layout
**Layout (1920×1080, OBS):** Isaac view left 2/3 · panel.html right 1/3 · webcam of Thiago's phone bottom right · Ferbin narrates live.
| Time | On screen | Said |
|---|---|---|
| 0:00 | 20 robots delivering; panel shows deliveries ticking | "This is Hyperion: 20 simulated robots in Isaac Sim, run by four agents." |
| 0:15 | We inject an overheat on robot 7 (button or curl) | "Robot 7 just overheated in aisle C." |
| 0:20 | Panel: DETECTED → DIAGNOSED (war room root cause) → POLICY OK | "Code requires a technician for hardware faults. The model writes the brief. Policy checks who we may contact." |
| 0:35 | FO CALLING; Thiago's phone rings on camera; Fo speaks (AI disclosure); Thiago: "I can be there in 15 minutes." | (let it play) |
| 1:30 | ETA 15 min; the allocator re-routes the aisle-C robots; deliveries keep going | "The fleet plans around the repair while the human is on the way." |
| 1:50 | CALENDAR ✔, EMAIL ✔; switch to the inbox and calendar for 5 s | "Real invite, real incident report." |
| 2:15 | Tech on site (demo clock), robot 7 cleared and driving; CLOSING EMAIL ✔ | "Downtime closed out automatically." |
| 2:35 | Panel trace: tool calls, policy checks, 0 violations; show `summary.csv` run count | "Robots are simulated. The call, calendar and emails were real. N runs, M succeeded." |

**Rehearsal checklist:** phone volume up, Do Not Disturb off, laptop on power, notifications muted, OBS recording to disk, a test recording played back with audio, `STOP` file absent, `contacts.yaml` = team only.

---

## 6. Trust by design: what we claim (all must be true)
1. **Allowlist in code:** the dispatcher can only reach contacts in `contacts.yaml`, all consenting teammates.
2. **Double check:** policy before building the request, and a PreToolUse hook on every Fo tool call (the model can't route around it).
3. **Least tools:** the Claude dispatcher process has only two tools (`ask_fo`, `get_fo_reply`), with no shell and no files.
4. **Rate limits:** 1 call / 3 emails / 1 event per incident, 3 incidents per run.
5. **Spend blocked** unless a human approves (the button).
6. **Kill switch:** the `STOP` file halts all outbound actions in under 1 s.
7. **Fallbacks are visible:** a bad ETA or Fo failure falls back to the timer and is logged, never hidden.
8. **Disclosure:** Fo announces it's an AI on a recorded line (Fo default).
9. **Full trace:** every decision and tool call is in `trace.jsonl`.

---

## 7. Submission package (drafts to finish Monday morning)
**X post (≤280 chars + video):**
> Hyperion × Fo: when one of 20 robots breaks, Claude gets a real technician on the way. Fo calls them, books the visit, emails the report, and the fleet re-plans until the fix. Robots simulated in Isaac Sim; call, calendar and emails real. @wajo_ai @AnthropicAI

**D. Write-up:**
- **Who it's for:** ops teams running robot fleets (warehouses, robots-as-a-service), where downtime waits on a human being called, scheduled and briefed.
- **Built at the event:** Claude backend for the agents, the Dispatcher (Claude + Fo), the policy layer and hook, the live agent loop with ETA feedback, the trace panel and run counter.
- **Existed before:** the Hyperion sim (20 Nova Carters, Isaac Sim), `fleet.py`, the four agents, the replay check, the sim bridge.
- **Mocked or simulated:** robots, faults and technician travel are simulated (demo clock shown on screen); the technician is a teammate. The phone call, calendar invite and emails are real.

**E. Build note:** tools = Claude (`claude -p`, Sonnet 5.5) + Fo MCP (`ask_fo`, `get_fo_reply`), Isaac Sim, Python. Added = dispatcher, policy hook, live loop, panel. Tried and didn't work = [fill honestly, e.g. "Fo can't call us back, so ETAs come from the call transcript, not a return call"].
**F. Run trace:** export one successful run's `trace.jsonl` (+ the stream-json of its dispatcher).
**G. Run count:** from `runs/summary.csv` ("N live runs, M succeeded; failures: …").

---

## 8. Risk register
| Risk | Likelihood | Mitigation |
|---|---|---|
| Fo calls slow (minutes) | medium | The sim keeps running; that IS the story. Panel shows elapsed time. Measure in A3 and set expectations in the narration |
| Fo can't call / call fails | low-medium | G1 → Plan B email dispatch; still real |
| `claude -p` can't use Fo headless (auth) | low (Connected ✔) | Fallback: dispatcher prompt run in an interactive Claude Code session, steps logged the same |
| Fo asks unexpected confirmations | medium | C6: auto-answer only policy-listed questions; else the human button |
| Isaac GUI slow on the laptop | medium | No arms (`noarms`), small window; fallback headless runner + a pre-rendered Isaac clip of the same run, said aloud |
| Event credits run out | low | Sonnet, short prompts; fallback to the logged-in Claude subscription; log which was used |
| Spam/consent concerns | low | Teammates only, consent in writing, AI disclosure, rate limits |
| Scope creep (arms, purchases, more robots) | high | Frozen at 1:30 AM. Arms = stretch, not in the take |

## 9. Stretch (only if G4 passes early)
- Spare-part order through Fo with the human approve button (shows the spend gate live, test card only).
- A second incident while the first tech is busy: the scheduler queues it and Fo calls the backup tech.
- Fo's same-day shift report as a Google Doc.

## 10. Housekeeping
- `~/hyperion-fo` is the only shared folder. Nothing from other projects goes in it.
- Secrets in `.env` (gitignored): the event API key. Phone numbers only in `contacts.yaml` (gitignored); the repo ships `contacts.example.yaml`.
- `git init`, commit at every gate, tag `demo-freeze`.
