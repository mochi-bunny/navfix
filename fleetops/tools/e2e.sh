#!/usr/bin/env bash
# e2e.sh <fault>: inject on a moving robot, time each hop through the deployed stack, then clear
FAULT=${1:-stuck}
BUSY=$(curl -s localhost:8090/tickets | python3 -c "import sys,json;print(','.join(str(t['robot_id']) for t in json.load(sys.stdin)['tickets'] if t['status']!='resolved' and t.get('robot_id') is not None))")
R=$(curl -s localhost:3001/api/telemetry | BUSY="$BUSY" python3 -c "import sys,json,os,random;b={int(x) for x in os.environ['BUSY'].split(',') if x};d=json.load(sys.stdin);c=[r for r in d['robots'] if r['vel']>0.5 and r['task'] in ('pick','carry') and r['id'] not in b];print(random.choice(c)['id'])")
NAME=$(printf "AMR-%02d" $R); t0=$(date +%s); db=/home/dell/navfix/navbot/data/bot.db
before=$(curl -s localhost:8090/tickets | python3 -c "import sys,json;print(len(json.load(sys.stdin)['tickets']))")
curl -s -X POST localhost:8090/demo/inject -H "Content-Type: application/json" -d "{\"robot\": $R, \"fault\": \"$FAULT\"}" >/dev/null
echo "t=0    injected $FAULT on $NAME"
det=""; tk=""; dash=""; disc=""
for i in $(seq 1 120); do
  now=$(( $(date +%s) - t0 ))
  [ -z "$det" ] && curl -s localhost:8090/events | R=$R python3 -c "import sys,json,os;r=int(os.environ['R']);sys.exit(0 if any(e.get('robot_id')==r or r in (e.get('robot_ids') or []) for e in json.load(sys.stdin)['events']) else 1)" && { det=1; echo "t=${now}s  supervisor detected it"; }
  if [ -z "$tk" ]; then T=$(curl -s localhost:8090/tickets | B=$before python3 -c "import sys,json,os;t=json.load(sys.stdin)['tickets'][int(os.environ['B']):];print(t[0]['id']+' '+t[0]['priority']+' | '+t[0]['reason'][:110] if t else '')"); [ -n "$T" ] && { tk=${T%% *}; echo "t=${now}s  agent opened $T"; }; fi
  if [ -n "$tk" ] && [ -z "$dash" ]; then curl -s localhost:8095/api/state | TK=$tk python3 -c "import sys,json,os;sys.exit(0 if any(n['need_id']==os.environ['TK'] for n in json.load(sys.stdin)['needs']) else 1)" && { dash=1; echo "t=${now}s  dashboard shows $tk"; }; fi
  if [ -n "$tk" ] && [ -z "$disc" ]; then n=$(python3 -c "import sqlite3;print(sqlite3.connect('file:$db?mode=ro',uri=True).execute(\"select count(*) from activity where kind='card_posted' and at > $t0\").fetchone()[0])"); [ "$n" -ge 2 ] && { disc=1; echo "t=${now}s  navbot posted $n Discord cards (#red-room + #approvals)"; }; fi
  [ -n "$dash" ] && [ -n "$disc" ] && break
  sleep 2
done
curl -s -X POST localhost:8090/demo/clear >/dev/null; echo "fault cleared"
