#!/usr/bin/env bash
# ask_agent.sh "question"  -> runs one supervisor-agent turn in a throwaway session, prints answer + timing
export PATH=$(ls -d ~/.nvm/versions/node/*/bin | head -1):$HOME/.local/bin:$PATH
s=$(date +%s.%N)
nemoclaw navfix agent --agent main --session-id "check-$RANDOM$RANDOM" --message "$1" --json --timeout 240 > /tmp/ask.out 2>&1
e=$(date +%s.%N)
python3 - "$s" "$e" <<'PY'
import sys, json
raw = open("/tmp/ask.out").read(); i = raw.find("\n{")
try:
    d = json.loads(raw[i + 1:])
except Exception:
    print("NO JSON:", raw[-400:]); raise SystemExit
def walk(o, p=""):
    if isinstance(o, dict):
        for k, v in o.items(): yield from walk(v, p + "." + k)
    elif isinstance(o, list):
        for j, v in enumerate(o): yield from walk(v, p + f"[{j}]")
    else: yield p, o
texts = [v for p, v in walk(d) if p.endswith(".text") and isinstance(v, str)]
think = [v for p, v in walk(d) if p.endswith("requestShaping.thinking")]
print(f"[{float(sys.argv[2]) - float(sys.argv[1]):.1f}s, thinking={think[0] if think else '?'}]")
print("\n".join(texts)[:1500])
PY
