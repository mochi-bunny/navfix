# navbot

navbot is the Fleet & Field Discord bot. The supervisor bot sends it events over HTTP, and navbot posts
them as cards in Discord channels. It also handles approvals: approvers react ✅ or ❌, and navbot sends
the decision back to the supervisor or tells the person who asked.

A local Qwen model writes optional one-line headlines and red-room debriefs. Every fact on a card
comes from the event, never from the model.

For the full list of APIs and services, see [setup.md](setup.md).

## What it posts where

| Event | Channel | Text from Qwen |
|---|---|---|
| `SKILL_REFUSED`, `TASK_FAILED` | `#red-room` | Debrief inside the card (skipped if the event has its own `debrief`) |
| `GATE_OPEN`, `JOB_DISPATCHED`, `JOB_COMPLETED` | `#dispatch-recall` | None (card only) |
| `APPROVAL_REQUEST` | `#approvals` | None (card only, with an `@approver` ping) |
| `DAILY_SUMMARY`, `DAILY_RECAP` | `#daily-checkin` | Headline above the card |
| `ETA_UPDATE` | `#cell-globex` | Headline above the card |
| `INFRA_ERROR` | `#ops-log` | Headline above the card |
| Telemetry CSV or fleet log (`POST /telemetry`) | `#monitor-bot` | None |
| Request typed by a person in `#approvals` | `#approvals` | None |

## 1. One-time setup

### Discord

1. In the [Developer Portal](https://discord.com/developers/applications), create an application, then
   create its bot and copy the token.
2. On the **Bot** tab, turn on **Message Content Intent**. Without it, navbot still posts cards, but it
   can't read requests people type in `#approvals`.
3. Invite the bot with these permissions: View Channels, Send Messages, Embed Links, Attach Files,
   Read Message History, Add Reactions and Manage Messages.
4. Create the channels: `#red-room`, `#dispatch-recall`, `#daily-checkin`, `#approvals`, `#cell-globex`,
   `#ops-log` and optionally `#monitor-bot`.
5. Create an `approver` role and give it to the people who may approve or deny.
6. Turn on **Developer Mode** (User Settings → Advanced). Then right-click each channel and the role,
   and pick **Copy ID**.

### Python

Use Python 3.12.

```bash
cd navbot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### Settings

```bash
cp .env.example .env
```

Fill in `.env`. Never commit it, because it holds the bot token.

| Setting | What to put |
|---|---|
| `DISCORD_TOKEN` | Bot token from the Developer Portal |
| `CH_*` | Channel IDs. `CH_MONITOR_BOT` is optional. |
| `ROLE_APPROVER` | ID of the `approver` role. Only people with this role can decide. |
| `ROLE_FIELD_ENGINEER`, `ROLE_CUSTOMER` | Optional. These roles are pinged on red-room and ETA cards. |
| `BOT_SHARED_KEY` | Shared secret. The supervisor sends it in the `X-Bot-Key` header. |
| `SUPERVISOR_DECISION_URL` | Where approval decisions are sent. For testing, use `http://127.0.0.1:8788/decisions`. |
| `LLM_URL`, `LLM_MODEL` | Qwen server and model ID. Set `LLM_ENABLED=0` to run without it. |

### Qwen (optional)

navbot works without a model; posts just go out without headlines or debriefs. To use one, start any
OpenAI-compatible server. For example, with llama.cpp:

```bash
llama-server -m qwen3.5-4b-Q4_K_M.gguf --port 8080 --alias qwen3.5-4b
```

Then set `LLM_URL=http://127.0.0.1:8080/v1` and `LLM_MODEL=qwen3.5-4b`. To check which model ID the
server uses, run `curl http://127.0.0.1:8080/v1/models`.

## 2. Run it

Run these from the `navbot` folder:

```bash
.venv/bin/python bot.py
```

To keep it running in the background, with logs going to `data/bot.log`:

```bash
nohup .venv/bin/python bot.py >> data/bot.log 2>&1 &
```

Check that it's up:

```bash
curl http://127.0.0.1:8787/health      # {"ok": true, "discord_ready": true}
```

The startup log line `bot_ready {'reads_messages': True}` means typed requests work.
`reads_messages: False` means the Message Content Intent is off.

**Restart the bot after changing `.env` or any `.py` file.** Settings and prompts are only loaded
at startup.

## 3. Try it with the mock supervisor

`mock_supervisor.py` stands in for the real supervisor. Open a second terminal:

```bash
.venv/bin/python mock_supervisor.py listen > data/mock.log 2>&1 &   # receives approval decisions
.venv/bin/python mock_supervisor.py send all        # one of each main card
.venv/bin/python mock_supervisor.py send red        # every #red-room variant
.venv/bin/python mock_supervisor.py send dispatch   # gate, dispatched, completed
.venv/bin/python mock_supervisor.py send book       # one approval request
.venv/bin/python mock_supervisor.py telemetry       # hourly robot health card
.venv/bin/python mock_supervisor.py fleetlog        # fleet log from samples/fleet_log_5min.json
```

### Send your own approval request

The `context` lines are what the approver reads. Each one appears on its own line on the card.

```bash
curl -X POST http://127.0.0.1:8787/events \
  -H "X-Bot-Key: $(grep ^BOT_SHARED_KEY .env | cut -d= -f2-)" -H 'Content-Type: application/json' \
  -d '{"event":"APPROVAL_REQUEST","request_id":"apr-'"$(date +%s)"'","kind":"WORK_PLAN","ticket_id":"T-12",
       "context":["Things to be done today at Globex:","1. Recalibrate GR1-07 gripper","2. Rerun the safety gate"],
       "options":[{"emoji":"✅","choice":"approve","label":"Approve"},
                  {"emoji":"❌","choice":"deny","label":"Deny"}]}'
```

`request_id` must be unique. If you reuse one, navbot returns `409`.

## 4. How approvals work

### Requests from the supervisor (`APPROVAL_REQUEST`)

1. navbot posts the card in `#approvals`, pings `@approver`, and adds one reaction per option.
2. The first person with the approver role to react decides. Reactions from anyone else are removed.
3. The card turns green (approved) or red (denied) and shows who decided and when.
4. navbot POSTs an `APPROVAL_DECISION` to `SUPERVISOR_DECISION_URL`. It tries 3 times; if all fail,
   the card says "not delivered" and `#ops-log` gets a warning.

### Requests people type

1. Someone posts a new message (not a reply) in `#approvals`, e.g. "Can I take Friday off?".
2. navbot replies with a **REQUEST · <type> · REQ-000N** card. The type (Time off, Schedule change,
   Site visit, Travel, Purchase, Access or Other) is chosen by the keywords in `approvals.py`.
3. An approver reacts ✅ or ❌. The card updates, navbot replies to the original message, and the
   requester also gets a **DM**. If they block DMs from server members, only the channel reply is sent.
4. Replies to a card count as conversation, not new requests.

## 5. Where things are recorded

Everything goes into `data/bot.db` (SQLite, WAL mode, so a dashboard can read while the bot writes):

- `events`: every card posted and every decision
- `approvals`: one row per request, with `status` (pending, approved or denied), `choice`,
  `decided_by_name`, `requested_by`, `details` and `delivered`
- `activity`: every message, reaction, card and DM, with who did it and when

```bash
sqlite3 data/bot.db "select request_id, kind, status, requested_by, decided_by_name from approvals order by requested_at desc limit 5"
```

`data/bot.log` has the same activity as readable lines, e.g. `request_created`, `request_decided`,
`requester_dm_sent`, `decision_delivered`.

## 6. Changing what Qwen writes

Everything is in `llm.py`:

| What | Where |
|---|---|
| Headline rules | `SYSTEM` |
| Debrief rules | `DEBRIEF_SYSTEM` |
| What each event means | `MEANING` |
| What each field means | `FIELDS` |
| Fields the model never sees | `HIDDEN` |
| Opening words of each headline (written by code) | `_opener()` |

Code drops any model text that contains a number not in the event. To see the exact prompt for a
sample event:

```bash
.venv/bin/python -c "import llm; from mock_supervisor import SAMPLES as S; e=S['eta']; print(llm._prompt(e, llm._opener(e)))"
```

To turn off headlines for a whole channel, add it to `CARD_ONLY` in `formatter.py`.

## 7. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `401` from `/events` | `X-Bot-Key` doesn't match `BOT_SHARED_KEY`. |
| `500 channel not visible` | Wrong `CH_*` ID, or the bot can't see that channel. |
| Reactions get removed, nothing is decided | The person doesn't have the role in `ROLE_APPROVER`, or it's still `000…`. |
| Typed requests are ignored; the log shows `content: ''` | Turn on Message Content Intent, then restart. |
| Request typed as "Other" | Add the wording to the right pattern in `KINDS` in `approvals.py`. |
| No headlines or debriefs | Qwen is down or slow. Check `curl $LLM_URL/models` and the `LLM … skipped` lines in the log. |
| `requester_dm_failed` in the log | The user blocks DMs from server members. They still get the channel reply. |
| Changes don't show up | Restart the bot. |
