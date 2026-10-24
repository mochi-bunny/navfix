#!/usr/bin/env python3
"""Claude Code PreToolUse hook: every mcp__fo__ask_fo call the dispatcher model makes is re-checked by policy.py.
Exit 2 blocks the tool call and tells the model why. The model cannot route around this: it runs in code, outside it."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import policy

ev = json.load(sys.stdin)
tool, inp = ev.get("tool_name", ""), ev.get("tool_input", {}) or {}
run_dir, incident = os.environ.get("HYPERION_RUN", "/tmp"), os.environ.get("HYPERION_INCIDENT", "none")
if tool.endswith(("get_fo_reply", "get_fo_conversation", "list_fo_conversations")):   # read-only
    sys.exit(0)
if not tool.endswith("ask_fo"):
    print(f"hyperion policy: tool {tool} is not allowed for the dispatcher", file=sys.stderr); sys.exit(2)
text = inp.get("message") if isinstance(inp.get("message"), str) else json.dumps(inp)   # check the real message text
ok, why = policy.check_message(text, run_dir, incident, allow_spend=os.environ.get("HYPERION_ALLOW_SPEND") == "1")
with open(os.path.join(run_dir, "hook.log"), "a") as f:
    f.write(json.dumps({"tool": tool, "ok": ok, "why": why, "chars": len(text)}) + "\n")
if not ok:
    print(f"hyperion policy blocked this Fo request: {why}", file=sys.stderr); sys.exit(2)
sys.exit(0)
