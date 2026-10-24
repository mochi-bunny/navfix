"""Dispatcher: gets a real human technician on the way through Fo.

Code builds the exact instruction to Fo (who, what, and nothing else) and checks it with policy.py.
A headless Claude (`claude -p`) with ONLY two tools, mcp__fo__ask_fo and mcp__fo__get_fo_reply, sends it,
polls Fo until the call is done, and reads the result back as JSON. A PreToolUse hook (policy_hook.py)
re-checks every Fo call the model makes. Code validates the ETA; anything odd falls back and is logged.
The whole stream (every tool call) is saved to runs/<id>/dispatch_<incident>.jsonl.
"""
import json, os, re, subprocess, tempfile, threading, time

import policy

HERE = os.path.dirname(os.path.abspath(__file__))
C = policy.CONTACTS
DEFAULT_ETA = 15
FO_TOOLS = "mcp__fo__ask_fo,mcp__fo__get_fo_reply"

SYSTEM = """You are the dispatcher of Hyperion, a simulated 20-robot warehouse used in a hackathon demo.
You have exactly two tools: ask_fo and get_fo_reply. Fo is a real assistant that makes real calls and sends real email.
Rules:
1. Call ask_fo ONCE with newConversation=true and the message given to you, copied VERBATIM. Do not add recipients or tasks.
2. If Fo's status is "working", keep calling get_fo_reply with the conversationId Fo returned, until the status is complete or failed.
3. If Fo asks a question: if it only asks you to confirm doing exactly what the message asked, answer with ask_fo
   (same conversationId): "Yes, go ahead exactly as described." For any other question, stop and report needs_human.
4. Never ask Fo to contact anyone else, buy anything, or do anything not in the message.
5. Finish with ONE JSON object and nothing else:
{"answered": true|false, "eta_min": <integer minutes the technician said, or null>, "call_summary": "<one sentence>",
 "calendar_done": true|false, "email_done": true|false, "calendar_conflict": true|false|null, "event_start": "<start time or null>",
 "needs_human": null|"<question>", "fo_status": "<complete|failed|...>"}"""


def call_message(inc, brief, run_tag, t):
    return (f"[Hyperion hackathon demo, run {run_tag}. The robots are simulated; this request is real.] "
            f"Phone {t['name']} at {t['phone']} now. Open the call with exactly: \"Emergency call from Hyperion. We need action "
            f"from {t['name']}.\" (keep your usual recorded-line notice). You are speaking to {t['name']} directly. Then read them this "
            f"brief word for word: \"{brief}\" Ask how many minutes until "
            f"they can be on site, "
            f"and accept whatever she answers. Keep the call under one minute. Do nothing else: no calendar, no email, "
            f"and do not contact anyone else even if she suggests someone.")


def confirm_message(inc, brief, eta, run_tag, t):
    o = C["ops_lead"]
    return (f"[Hyperion hackathon demo, run {run_tag}. Simulated robots, real calendar and email.] Three things:\n"
            f"1. Check the calendar of {o['email']} for anything between {eta} and {eta + 30} minutes from now. Do not change "
            f"existing events.\n"
            f"2. Create a 30-minute event titled \"Hyperion: robot {inc['robot']} repair ({inc['id']})\" starting {eta} minutes "
            f"from now, or, if step 1 found a conflict, starting right after the conflicting event ends. Invite {o['email']}.\n"
            f"3. Email {o['email']} with subject \"Hyperion {inc['id']}: robot {inc['robot']} {inc['code']}, technician en route\" "
            f"and body \"{brief} Technician: {t['name']}, on site in {eta} minutes (confirmed by phone). Repair slot: <the event's start "
            f"time>.\"\nNo other actions. In your reply say whether there was a conflict and the event's start time.")


def escalate_message(inc, said, run_tag):
    o = C["ops_lead"]
    return (f"[Hyperion hackathon demo, run {run_tag}. Simulated robots, real email.] Email {o['email']} with subject "
            f"\"Hyperion {inc['id']}: robot {inc['robot']} needs a human decision\" and body \"The on-call technician "
            f"chain ({', '.join(t['name'] for t in techs())}) could not commit to a time. What they said: {said[:240]} Hyperion will not contact "
            f"anyone outside the approved list. Robot {inc['robot']} stays out of service; the fleet is routing around aisle "
            f"{inc['zone']}. Approve a next step on the ops panel.\" No other actions.")


def close_message(inc, downtime_s, run_tag, who):
    o = C["ops_lead"]
    return (f"[Hyperion hackathon demo, run {run_tag}. Simulated robots, real email.] Email {o['email']} with subject "
            f"\"Hyperion {inc['id']} closed: robot {inc['robot']} back in service\" and body \"Robot {inc['robot']} is delivering "
            f"again. Downtime {downtime_s:.0f} s (demo clock). Repaired by: {who}.\" No other actions.")


def techs():
    return [t for t in C["technicians"] if t.get("phone")]          # only people with a number AND consent are callable


def _settings(run_dir):
    p = os.path.join(run_dir, "dispatcher_settings.json")
    json.dump({"hooks": {"PreToolUse": [{"matcher": "mcp__fo__.*", "hooks": [
        {"type": "command", "command": f"python3 {os.path.join(HERE, 'policy_hook.py')}"}]}]}}, open(p, "w"))
    return p


def run_fo(message, run_dir, inc_id, kind, emit, dry=False, timeout=900):
    """Pre-check the message, then let the dispatcher model send it to Fo and poll. Returns the parsed JSON (or None)."""
    run_dir = os.path.abspath(run_dir)              # the dispatcher runs from a temp dir: paths must be absolute
    ok, why = policy.check_message(message, run_dir, inc_id, record=False)      # same counters the hook enforces
    emit("policy", "precheck", incident=inc_id, step=kind, ok=ok, why=why)
    if not ok:
        return {"blocked": why}
    if dry:
        emit("dispatcher", "dry_run", incident=inc_id, step=kind, fo_message=message)
        time.sleep(4)
        return {"answered": True, "eta_min": DEFAULT_ETA, "call_summary": "DRY RUN: no call placed", "calendar_done": False,
                "email_done": False, "needs_human": None, "fo_status": "dry_run"}
    env = dict(os.environ, HYPERION_RUN=run_dir, HYPERION_INCIDENT=inc_id)
    prompt = "Send this message to Fo, verbatim:\n<<<\n" + message + "\n>>>"
    cmd = ["claude", "-p", prompt, "--model", os.environ.get("DISPATCH_MODEL", "sonnet"), "--output-format", "stream-json",
           "--verbose", "--append-system-prompt", SYSTEM, f"--allowedTools={FO_TOOLS}",
           "--disallowedTools=Bash,Edit,Write,Read,WebFetch,WebSearch,Glob,Grep,Task,NotebookEdit",
           "--settings", _settings(run_dir), "--max-turns", "60"]
    log = open(os.path.join(run_dir, f"dispatch_{inc_id}_{kind}.jsonl"), "w")
    t0 = time.time(); result = None; conv_id = [None]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, cwd=tempfile.gettempdir())
    timer = threading.Timer(timeout, p.kill); timer.start()
    for line in p.stdout:
        log.write(line); log.flush()
        try: d = json.loads(line)
        except ValueError: continue
        if d.get("type") == "assistant":
            for c in d["message"].get("content", []):
                if c.get("type") == "tool_use":
                    inp = c.get("input", {})
                    emit("dispatcher", "fo_tool_call", incident=inc_id, step=kind, tool=c["name"].split("__")[-1],
                         input={k: (v[:160] if isinstance(v, str) else v) for k, v in inp.items()})
        elif d.get("type") == "user":
            for c in d["message"].get("content", []):
                if c.get("type") == "tool_result":
                    txt = c.get("content")
                    txt = " ".join(x.get("text", "") for x in txt) if isinstance(txt, list) else str(txt)
                    m = re.search(r'"(?:status)"\s*:\s*"(\w+)"', txt); pr = re.search(r'"progress"\s*:\s*"([^"]{0,160})', txt)
                    cm = re.search(r'"conversationId"\s*:\s*"([0-9a-f-]{36})"', txt)
                    if cm: conv_id[0] = cm.group(1)
                    emit("fo", "reply", incident=inc_id, step=kind, status=m.group(1) if m else ("blocked" if c.get("is_error") else "?"),
                         progress=pr.group(1) if pr else txt[:160], is_error=bool(c.get("is_error")))
        elif d.get("type") == "result":
            result = d
    p.wait(); timer.cancel(); log.close()
    text = (result or {}).get("result", "") or ""
    mobj = re.search(r"\{.*\}", text, re.S)
    try: out = json.loads(mobj.group(0)) if mobj else None
    except ValueError: out = None
    if out is not None: out["_conversation_id"] = conv_id[0]
    emit("dispatcher", "fo_done", incident=inc_id, step=kind, seconds=round(time.time() - t0, 1),
         cost_usd=(result or {}).get("total_cost_usd"), parsed=out is not None, result=out or text[:300])
    return out


POLL_PROMPT = """Call list_fo_conversations once. Find the NEWEST conversation whose channel is "voice" and whose startedAt
is later than {since}. If there is one, call get_fo_conversation on it once. Then reply with ONE JSON object only:
{{"found": true|false, "call_finished": true|false (the assistant said goodbye/thanks or the person hung up),
 "decision": "accepted"|"declined"|"unclear"|"no_answer", "eta_min": <minutes she committed to, as an integer, or null>,
 "said": "<her words about timing, verbatim, max 200 chars>", "suggested_other_person": "<name or null>"}}"""


def poll_call(since_iso, run_dir, inc_id, emit, every=15, max_wait=300):
    """Fo places the call asynchronously and logs it as its own voice conversation. Code polls (read-only tools,
    still through the policy hook) until that call has finished, then returns the model's reading of it."""
    t0 = time.time()
    while time.time() - t0 < max_wait:
        time.sleep(every)
        env = dict(os.environ, HYPERION_RUN=run_dir, HYPERION_INCIDENT=inc_id)
        cmd = ["claude", "-p", POLL_PROMPT.format(since=since_iso), "--model", "sonnet", "--output-format", "json",
               "--allowedTools=mcp__fo__list_fo_conversations,mcp__fo__get_fo_conversation",
               "--settings", _settings(run_dir), "--max-turns", "5"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env, cwd=tempfile.gettempdir())
            m = re.search(r"\{.*\}", json.loads(r.stdout).get("result", ""), re.S)
            obj = json.loads(m.group(0)) if m else None
        except (subprocess.TimeoutExpired, ValueError, OSError, AttributeError):
            obj = None
        emit("fo", "call_status", incident=inc_id, status=("no call yet" if not obj or not obj.get("found") else
             ("finished" if obj.get("call_finished") else "in progress")), progress=(obj or {}).get("said") or "",
             elapsed_s=round(time.time() - t0))
        if obj and obj.get("found") and obj.get("call_finished"):
            return obj
    return None


def dispatch_technician(inc, brief, run_dir, run_tag, emit, dry=False):
    """Call the technicians in order until one commits to a time. -> (eta_min or None, outcome, who).
    None = nobody committed: the incident is escalated to a human; nobody outside the allowlist is ever contacted."""
    said = []
    for i, t in enumerate(techs()):
        since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 5))
        emit("dispatcher", "calling", incident=inc["id"], summary=f"{t['name']} ({'on-call' if i == 0 else 'backup'})")
        sent = run_fo(call_message(inc, brief, run_tag, t), run_dir, inc["id"], f"call_{t['name'].lower()}", emit, dry)
        if dry:   # DRY_SCRIPT=decline,accept  -> scripted answers per technician, for testing both paths
            script = os.environ.get("DRY_SCRIPT", "accept").split(",")
            dec = script[min(i, len(script) - 1)]
            outcome = ({"decision": "accepted", "eta_min": DEFAULT_ETA, "said": "DRY RUN: I can be there in 15 minutes", "call_finished": True}
                       if dec == "accept" else {"decision": "declined", "eta_min": None, "said": "DRY RUN: not available today", "call_finished": True})
        elif not sent or sent.get("blocked"):
            outcome = {"decision": "blocked", "said": (sent or {}).get("blocked", "Fo request failed")}
        else:
            outcome = poll_call(since, run_dir, inc["id"], emit) or {"decision": "unclear", "said": "no finished call found in 5 min"}
        eta = outcome.get("eta_min")
        ok = outcome.get("decision") == "accepted" and isinstance(eta, (int, float)) and 1 <= eta <= 120   # code decides
        emit("dispatcher", "call_outcome", incident=inc["id"], decision=outcome.get("decision"), eta_min=eta if ok else None,
             summary=f"{t['name']}: {outcome.get('said', '')}", suggested_other=outcome.get("suggested_other_person"), accepted=ok)
        if ok:
            res = run_fo(confirm_message(inc, brief, int(eta), run_tag, t), run_dir, inc["id"], "confirm", emit, dry)
            emit("dispatcher", "calendar", incident=inc["id"], ok=bool(res and res.get("calendar_done")),
                 summary=f"conflict: {(res or {}).get('calendar_conflict')} · start: {(res or {}).get('event_start')}")
            return int(eta), outcome, t["name"]
        said.append(f"{t['name']}: {outcome.get('said', '')}")
    run_fo(escalate_message(inc, " | ".join(said), run_tag), run_dir, inc["id"], "escalate", emit, dry)
    return None, {"decision": "escalated", "said": " | ".join(said)}, None


def close_out(inc, downtime_s, run_dir, run_tag, emit, dry=False, who=None):
    return run_fo(close_message(inc, downtime_s, run_tag, who or "technician"), run_dir, inc["id"], "close", emit, dry)
