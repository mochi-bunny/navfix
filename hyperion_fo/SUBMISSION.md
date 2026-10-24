# Hyperion × Fo: submission (Wajo × Anthropic "Get Sh*t Done")

Team: Megha, Ferbin, Thiago.
Deadlines: X quote-repost **Mon Oct 19, 10:15 AM PT** (1:15 PM ET). Discord form **Tue Oct 20, 10:15 AM PT**.

## Files
| What | File |
|---|---|
| **X post video** | `Hyperion_x_Fo_X.mp4` (2:19, fits X's 2:20 non-Premium cap; all 3 failure modes + Isaac Sim explainer). Post texts: `X_POSTS.md` |
| **Video (send this)** | `Hyperion_x_Fo_v3.mp4` (3:35) / `_v3_share.mp4` (16 MB): problem -> idea -> 4 steps (Detect, Decide, Dispatch, Recover) -> real run (real Isaac Sim app, real call audio, real Gmail) -> Fo can vs Hyperion's rules -> 3 failure modes (no time, someone off the list, 67 days) -> close. Earlier cuts: `Hyperion_x_Fo_REAL_v2.mp4`, `Hyperion_x_Fo_REAL_3min.mp4` |
| **C. Unedited take (send this)** | `UNEDITED_take_REAL_ISAAC_GUI_run.mp4` (10:28, one take of the live screen: Isaac Sim GUI + ops panel + Gmail) |
| Call recordings | `call_audio/*.mp3` (downloaded from the Wajo dashboard) |
| Older takes / cut | `UNEDITED_take_hero_run.mp4`, `UNEDITED_take_final_run.mp4`, `older_cut/` |
| Unedited take (escalation path) | `UNEDITED_take_final_run.mp4` (5:21) |
| **F. Run trace** | `trace_hero_run/TRACE_READABLE.md` (+ raw `trace.jsonl`, the dispatcher's full Claude stream `dispatch_*.jsonl`, `policy_state.json`, `hook.log`) |
| **G. Run count** | `run_count.csv` |
| Call transcripts (verbatim from Fo) | `CALL_TRANSCRIPTS.md` |

## 1. X post (quote-repost Shivani's 10 AM PT post)
> When one of our 20 robots breaks, Claude gets a real person on the way. Fo calls the on-call tech, moves down the list when she says no, checks the calendar, books the repair and emails the report, while the fleet routes around the broken robot.
> Robots: NVIDIA Isaac Sim. Calls, calendar, email: real. 🧵
> Built by Megha, Ferbin & Thiago @wajo_ai @AnthropicAI

## 2. Form field D: short write-up
**Who it's for:** teams running robot fleets (warehouses, robots-as-a-service). When a robot breaks, the repair is rarely the hard part. Getting a person there is: someone has to notice, call around, find who's free, book it and brief them. Today that's done by hand.

**What we built at the event:**
- A live agent loop (`ops_live.py`) that reads the Hyperion fleet from the sim bridge and runs our four agents on Claude.
- A Claude **dispatcher** that, when code decides a hardware fault needs a human, sends Fo one exact instruction. It uses `claude -p` with only Fo's tools.
- The dispatch chain:
  - Fo phones the on-call technician.
  - Code reads the call transcript.
  - If they decline, Fo calls the backup.
  - Fo checks the calendar and books the repair slot.
  - Fo emails the incident report.
  - The technician's ETA becomes the repair timer in the sim.
  - The robot is cleared on arrival and Fo sends the closing email.
  - If nobody commits, the incident escalates to a human: an email plus an approve button.
- A **policy layer**: an allowlist, max 2 calls per incident and 1 per person, no calling people by name, spend blocked, a kill switch. It's enforced twice: before the request is built, and by a **PreToolUse hook on every `mcp__fo__*` call** the model makes.
- An ops panel with live Isaac Sim camera streams, plus a run trace and run counter.

**What existed before:** the Hyperion warehouse sim (20 Nova Carters in NVIDIA Isaac Sim, `fleet.py`), the four agents (surveillance, war room, scheduler, allocator), the sim bridge, and the replay check.

**What's mocked or simulated:**
- The robots, faults and technician travel are simulated. The 20 Nova Carters are rendered live in NVIDIA Isaac Sim and moved by our fleet code each frame (kinematic: Isaac physics and the onboard cameras are off for the fleet). The demo clock runs at 2 sim-seconds per real minute of ETA.
- The technicians are teammates who agreed to be called.
- In the final run the "ops lead approve" click was done by the operator.
- **Real:** the phone calls, the calendar check and booking, and the emails, all through Fo.

## 3. Form field E: build note
- **Tools the agent uses:** Claude via the Claude Code CLI (`claude -p`, Sonnet) with **only** `mcp__fo__ask_fo`, `get_fo_reply`, `list_fo_conversations` and `get_fo_conversation`. Fo does the calls, Gmail and Calendar.
- **What we added on top:** a code-built Fo request per step, a PreToolUse policy hook, polling of the voice conversation for the call outcome, an ETA validator (1-120 min), a backup chain, escalation, Isaac Sim ETA feedback, and the panel.
- **One thing we tried that didn't work:**
  - We first asked Fo to call, book and email in one message. Fo finishes its chat turn *before* the phone call ends, and the call result lands in a separate voice conversation. So the dispatcher never saw the answer: it fell back to a timer and Fo sent a wrong "repaired" email.
  - We split it into steps and now poll the voice transcript, and a "repaired" email only goes out after a confirmed arrival.
  - Our own hook also once blocked a legitimate escalation email, because "on-call" matched the call rule. Fixed.

## 4. Form field G: run count
6 live runs, 10 real phone calls (latest two on the real Isaac Sim GUI; see run_count.csv for gui1/gui2):
| Run | Result | What happened |
|---|---|---|
| run 1 | FAIL | Calls worked. Megha: "a day... send Amal instead"; Fo refused (Amal is not approved). Found 2 bugs: missed the call result, and paged a human on a low battery. Both fixed. |
| hero | **SUCCESS** | Megha declined, then Thiago said "67 minutes". Calendar checked (no conflict), repair booked for 9:22 PM ET, incident email sent, robot cleared, closing email sent. |
| final | **SUCCESS (escalation)** | Both calls opened with "Emergency call from Hyperion". Megha declined; Thiago said "sixty seven days", which code rejected (valid ETA is 1-120 min). Escalated to a human, approved, robot cleared, closing email sent. The escalation email was blocked by our hook (bug). **Fixed and re-tested live at 20:55 ET:** the same escalation message passed the hook and Fo sent it ("Hyperion INC-1: robot 7 needs a human decision"). |

## 5. Form field F: run trace
`trace_real_gui_run/` (the run in the video: trace.jsonl + every dispatcher Claude stream) and `trace_hero_run/TRACE_READABLE.md` has 49 rows: detection, war room, brief, policy checks, each Fo tool call, call outcomes, calendar, en route, repair, close. The raw Claude stream for each dispatcher step (every tool call and result) is in `dispatch_INC-1_*.jsonl`.

## Honest notes
- Discord: Fo has no Discord connection. The code supports a webhook (`discord.py`), but we didn't have one in time, so the runs show "Discord skipped".
- A stray calendar hold ("INC-19", 8:25 PM) and email came from run 1's low-battery bug.
- The 3-min video's narration is a synthetic voice (Chatterbox). The call quotes on screen are verbatim from Fo's transcripts.
