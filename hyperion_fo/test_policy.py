"""Regression test: every message the dispatcher sends goes through the REAL hook process (same stdin JSON Claude Code sends)."""
import json, os, subprocess, tempfile, dispatch
inc = {'id': 'INC-1', 'robot': 7, 'zone': 1, 'code': 'DRIVE_FAULT'}; T = dispatch.techs(); d = tempfile.mkdtemp()
def hook(msg):
    ev = {"tool_name": "mcp__fo__ask_fo", "tool_input": {"message": msg, "newConversation": True}}
    r = subprocess.run(["python3", "policy_hook.py"], input=json.dumps(ev), text=True, capture_output=True,
                       env=dict(os.environ, HYPERION_RUN=d, HYPERION_INCIDENT="INC-1"))
    return r.returncode == 0, r.stderr.strip()
cases = [("call megha", dispatch.call_message(inc, "b", "t", T[0]), True), ("call thiago", dispatch.call_message(inc, "b", "t", T[1]), True),
         ("confirm (cal+email)", dispatch.confirm_message(inc, "Robot 7 stopped.", 50, "t", T[1]), True),
         ("close email", dispatch.close_message(inc, 90, "t", "Thiago"), True),
         ("3rd call", dispatch.call_message(inc, "b", "t", T[0]), False), ("call by name", "[Hyperion t] Call Amal now", False),
         ("off-list email", "[Hyperion t] Email boss@corp.com", False), ("spend", "[Hyperion t] Order a spare motor and email someone@example.com", False)]
bad = 0
for name, msg, want in cases:
    ok, err = hook(msg); bad += ok != want
    print(f"{'PASS' if ok == want else 'FAIL'}  {name:<22} allowed={ok}  {err[:90]}")
d2 = tempfile.mkdtemp()   # escalation path in a fresh incident: 2 calls then the escalation email
for name, msg in [("call megha", dispatch.call_message(inc, "b", "t", T[0])), ("call thiago", dispatch.call_message(inc, "b", "t", T[1])),
                  ("escalate email", dispatch.escalate_message(inc, "Megha: no | Thiago: 67 days", "t"))]:
    ev = {"tool_name": "mcp__fo__ask_fo", "tool_input": {"message": msg}}
    r = subprocess.run(["python3", "policy_hook.py"], input=json.dumps(ev), text=True, capture_output=True, env=dict(os.environ, HYPERION_RUN=d2, HYPERION_INCIDENT="INC-1"))
    ok = r.returncode == 0; bad += not ok; print(f"{'PASS' if ok else 'FAIL'}  esc/{name:<18} allowed={ok}  {r.stderr.strip()[:90]}")
print("ALL PASS" if not bad else f"{bad} FAILURES")
