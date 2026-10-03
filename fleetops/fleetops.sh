#!/usr/bin/env bash
# FleetOps stack control on the GB10.
#
#   ./fleetops.sh start   [component|all]   start in dependency order, health-checking each one
#   ./fleetops.sh stop    [component|all]   stop (the model server and the agent sandbox are left alone)
#   ./fleetops.sh restart [component|all]
#   ./fleetops.sh status                    one line per component
#   ./fleetops.sh logs    <component>       follow a component's log
#   ./fleetops.sh build                     rebuild the image after a code change and redeploy
#   ./fleetops.sh isaac                     Isaac Sim GUI on this screen becomes the sim (headless sim stops)
#   ./fleetops.sh headless                  close Isaac, headless sim back
#   ./fleetops.sh reset-demo                back up and clear tickets + trust before a demo run
#
# Components, in start order:
#   model       vLLM serving Qwen3.6-35B-A3B (docker container vllm-qwen, port 8000)   [start/status only]
#   bridge      sim bridge: serves the sim to everyone (port 3001)
#   sim         headless fleet sim, pushes to the bridge (no port)
#   supervisor  supervisor tool service: detectors, tickets, trust gate (port 8090)
#   agent       supervisor LLM agent: OpenClaw in the NemoClaw sandbox "navfix"     [status only]
#   glue        FleetOps glue: dashboard sources, Discord forwarding, live fleet log (port 7100)
#   dashboard   command-center dashboard (port 8095, open to the LAN)
#   navbot      Discord bot / scheduler side (port 8787)
#
# Every service runs exec'd inside its own tmux session "fo-<component>", so stopping the session
# stops exactly that process. Logs: ~/fleetops/logs/<component>.log
set -uo pipefail
FO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUP="$HOME/hack/src/app"          # supervisor tool service + skill (git repo)
NAV="$HOME/navfix"                # dashboard + navbot (Megha / Amal's repo)
LOGS="$FO/logs"; mkdir -p "$LOGS" "$FO/run"
ORDER=(model bridge sim supervisor agent glue dashboard navbot)

secret() { cat "$FO/secrets/$1" 2>/dev/null; }
navkey() { grep '^BOT_SHARED_KEY=' "$NAV/navbot/.env" 2>/dev/null | cut -d= -f2-; }

port_of()   { case $1 in model) echo 8000;; bridge) echo 3001;; supervisor) echo 8090;; agent) echo 18789;;
                                 glue) echo 7100;; dashboard) echo 8095;; navbot) echo 8787;; *) echo "";; esac; }
health_of() { case $1 in model) echo "http://127.0.0.1:8000/v1/models";; bridge) echo "http://127.0.0.1:3001/health";;
                         supervisor) echo "http://127.0.0.1:8090/health";; glue) echo "http://127.0.0.1:7100/health";;
                         dashboard) echo "http://127.0.0.1:8095/api/config";; navbot) echo "http://127.0.0.1:8787/health";;
                         *) echo "";; esac; }

cmd_of() {
  case $1 in
    bridge) echo "cd '$FO/sim' && exec env BRIDGE_TOKEN='$(secret bridge_token)' python3 sim_bridge.py";;
    sim) echo "cd '$FO/sim' && exec env BRIDGE_TOKEN='$(secret bridge_token)' python3 fleet_runner.py --bridge http://127.0.0.1:3001 --seed ${FLEET_SEED:-7}";;
    supervisor) echo "cd '$SUP' && exec env SUPERVISOR_WEBHOOK_URL=http://127.0.0.1:18789/hooks/agent SUPERVISOR_THINKING=${SUPERVISOR_THINKING:-off} SCHEDULER_WEBHOOK_URL=http://127.0.0.1:7100/hooks/scheduler python3 tools_service.py";;
    glue) echo "cd '$FO/glue' && exec env NAVBOT_URL=http://127.0.0.1:8787 BOT_SHARED_KEY='$(navkey)' ISAAC_STREAM_URL='${ISAAC_STREAM_URL:-}' python3 fleetops_glue.py";;
    dashboard) echo "cd '$NAV/dashboard' && exec python3 run.py --config config.fleetops.json --mode live";;
    navbot) echo "cd '$NAV/navbot' && exec .venv/bin/python bot.py";;
  esac
}

pid_on_port() { ss -ltnp 2>/dev/null | grep -E "[:.]$1 " | grep -oP 'pid=\K[0-9]+' | head -1; }

healthy() {
  local c=$1 url; url=$(health_of "$c")
  case $c in
    sim)   curl -s --max-time 2 http://127.0.0.1:3001/health | python3 -c "import sys,json;d=json.load(sys.stdin);sys.exit(0 if d.get('ok') and (d.get('stale_s') or 99)<3 else 1)" 2>/dev/null;;
    agent) docker ps --format '{{.Names}} {{.Status}}' | grep -q 'openshell-default--navfix.*healthy' && [ -n "$(pid_on_port 18789)" ];;
    navbot) curl -s --max-time 2 "$url" | grep -q '"discord_ready": true';;
    *)     [ -n "$url" ] && curl -s -o /dev/null --max-time 2 -f "$url";;
  esac
}

start_one() {
  local c=$1 i
  case $c in
    model) healthy model && { echo "  model       already up"; return 0; }
           docker start vllm-qwen >/dev/null && echo "  model       starting (loading weights takes a few minutes)"
           for i in $(seq 1 120); do healthy model && { echo "  model       up"; return 0; }; sleep 5; done
           echo "  model       NOT healthy after 10 min"; return 1;;
    agent) healthy agent && echo "  agent       up (NemoClaw sandbox navfix)" || \
             echo "  agent       DOWN: run 'nemoclaw navfix start' (then ~/fleetops/enable_thinking.sh)"; return 0;;
  esac
  if healthy "$c"; then echo "  $(printf %-11s $c) already up"; return 0; fi
  local p; p=$(port_of "$c")
  if [ -n "$p" ] && [ -n "$(pid_on_port "$p")" ]; then
    echo "  $(printf %-11s $c) port $p is held by pid $(pid_on_port "$p") outside fleetops.sh: stop it first"; return 1
  fi
  tmux kill-session -t "fo-$c" 2>/dev/null
  echo "=== $(date '+%F %T') start ===" >> "$LOGS/$c.log"
  tmux new -d -s "fo-$c" "bash -c \"$(cmd_of "$c") >> '$LOGS/$c.log' 2>&1\""
  for i in $(seq 1 40); do healthy "$c" && { echo "  $(printf %-11s $c) up"; return 0; }; sleep 0.5; done
  echo "  $(printf %-11s $c) FAILED to become healthy; last log lines:"; tail -5 "$LOGS/$c.log" | sed 's/^/      /'; return 1
}

stop_one() {
  local c=$1 i
  case $c in model|agent) echo "  $(printf %-11s $c) left running (shared; stop it by hand if you mean to)"; return 0;; esac
  tmux kill-session -t "fo-$c" 2>/dev/null
  local p; p=$(port_of "$c")
  for i in $(seq 1 20); do { [ -z "$p" ] || [ -z "$(pid_on_port "$p")" ]; } && break; sleep 0.25; done
  if [ -n "$p" ] && [ -n "$(pid_on_port "$p")" ]; then
    echo "  $(printf %-11s $c) still on port $p (pid $(pid_on_port "$p"), not started by fleetops.sh): left alone"
  else echo "  $(printf %-11s $c) stopped"; fi
}

status() {
  printf "%-11s %-7s %-6s %-8s %s\n" COMPONENT HEALTH PORT PID DETAIL
  for c in "${ORDER[@]}"; do
    local p pid h d=""; p=$(port_of "$c"); pid=$([ -n "$p" ] && pid_on_port "$p")
    healthy "$c" && h=ok || h=DOWN
    case $c in
      sim) d=$(curl -s --max-time 2 http://127.0.0.1:3001/api/telemetry | python3 -c "import sys,json;d=json.load(sys.stdin);print(f\"sim t={d['t']:.0f}s, {sum(r['vel']>0.05 for r in d['robots'])}/{len(d['robots'])} moving, {d.get('deliveries')} deliveries\")" 2>/dev/null);;
      supervisor) d=$(curl -s --max-time 2 http://127.0.0.1:8090/metrics | python3 -c "import sys,json;m=json.load(sys.stdin);t=m['tickets'];print(f\"{m['robots']['with_active_problems']} robots with problems, tickets open {t['open']} / resolved {t['resolved']}\")" 2>/dev/null);;
      glue) d=$(curl -s --max-time 2 http://127.0.0.1:7100/health | python3 -c "import sys,json;d=json.load(sys.stdin);print(f\"navbot sent {d['navbot_sent']} failed {d['navbot_failed']}, {d['events_buffered']} events buffered\")" 2>/dev/null);;
      model) d=$(curl -s --max-time 2 http://127.0.0.1:8000/v1/models | python3 -c "import sys,json;print(json.load(sys.stdin)['data'][0]['id'])" 2>/dev/null);;
      dashboard) d="http://$(hostname -I | awk '{print $1}'):8095";;
    esac
    printf "%-11s %-7s %-6s %-8s %s\n" "$c" "$h" "${p:--}" "${pid:--}" "$d"
  done
}

targets() { if [ -z "${1:-}" ] || [ "$1" = all ]; then echo "${ORDER[@]}"; else echo "$1"; fi; }

COMPOSE=(docker compose -f "$FO/docker/compose.yaml")
compose_svc() { case $1 in model|agent) echo "";; *) echo "$1";; esac; }

case "${1:-status}" in
  # The stack runs in Docker (docker/compose.yaml). start/stop/logs drive compose; the model server and the
  # agent sandbox are separate containers and are only checked here.
  start)   healthy model || start_one model; "${COMPOSE[@]}" up -d ${2:+$(compose_svc "$2")}; sleep 2; start_one agent; status ;;
  stop)    "${COMPOSE[@]}" stop ${2:+$(compose_svc "$2")} ;;
  restart) "${COMPOSE[@]}" restart ${2:+$(compose_svc "$2")}; sleep 3; status ;;
  build)   "${COMPOSE[@]}" build && "${COMPOSE[@]}" up -d ;;
  status)  status; echo; "${COMPOSE[@]}" ps --format "table {{.Service}}\t{{.Status}}" ;;
  logs)    "${COMPOSE[@]}" logs -f --tail 40 "${2:?component}" ;;
  tmux-start) for c in $(targets "${2:-}"); do start_one "$c" || exit 1; done ;;   # old non-container mode
  isaac)   # Isaac Sim GUI on this box's screen becomes the sim: stop the headless sim first (one source only)
           "${COMPOSE[@]}" stop sim
           tmux kill-session -t fo-isaac 2>/dev/null
           echo "=== $(date '+%F %T') isaac start ===" >> "$LOGS/isaac.log"
           tmux new -d -s fo-isaac "bash -c 'export DISPLAY=${ISAAC_DISPLAY:-:1} XAUTHORITY=/run/user/$(id -u)/gdm/Xauthority OMNI_KIT_ACCEPT_EULA=YES; exec $HOME/isaacsim-env/bin/python $FO/isaac/warehouse_live.py --bridge http://127.0.0.1:3001 --cams --seed ${FLEET_SEED:-7} >> $LOGS/isaac.log 2>&1'"
           echo "  isaac       starting on display ${ISAAC_DISPLAY:-:1} (first launch compiles shaders: a few minutes)"
           for i in $(seq 1 120); do curl -s --max-time 2 http://127.0.0.1:3001/health | grep -q '"source": "isaac"' && { echo "  isaac       live: pushing to the bridge"; exit 0; }; sleep 5; done
           echo "  isaac       not pushing after 10 min; last log lines:"; tail -5 "$LOGS/isaac.log" ;;
  headless) tmux kill-session -t fo-isaac 2>/dev/null && echo "  isaac       closed"; "${COMPOSE[@]}" start sim; sleep 3; status ;;
  reset-demo) # clean slate before a run: back up, then clear tickets and trust history
           b="$SUP/data/backup-$(date +%H%M%S)"; mkdir -p "$b"
           cp -a "$SUP/data/tickets.json" "$SUP/data/trust.json" "$b/" 2>/dev/null
           "${COMPOSE[@]}" stop supervisor glue
           rm -f "$SUP/data/tickets.json" "$SUP/data/trust.json"
           "${COMPOSE[@]}" start supervisor glue; sleep 4; echo "  tickets and trust cleared (backup: $b)"; status ;;
  *)       sed -n '2,25p' "$0"; exit 1 ;;
esac
