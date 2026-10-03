#!/usr/bin/env bash
# Turn Qwen reasoning on for the OpenClaw agents in the NemoClaw sandbox (default: navfix).
# Needed again after `nemoclaw <sandbox> rebuild` or `channels add`, which regenerate openclaw.json.
# Hot-reloads: no gateway restart. Undo: set thinkingDefault back to "off".
set -euo pipefail
SB="${1:-navfix}"
C=$(docker ps --format '{{.Names}}' | grep "openshell-default--$SB" | head -1)
[ -n "$C" ] || { echo "sandbox container for $SB not found"; exit 1; }
mkdir -p ~/fleetops/config-backups
docker exec "$C" cat /sandbox/.openclaw/openclaw.json > ~/fleetops/config-backups/openclaw.json.$(date +%Y%m%d-%H%M%S)
docker cp ~/fleetops/thinking.patch.json5 "$C":/tmp/thinking.patch.json5
docker exec "$C" chmod 644 /tmp/thinking.patch.json5
docker exec -u sandbox -e HOME=/sandbox "$C" openclaw config patch --file /tmp/thinking.patch.json5
docker exec -u sandbox -e HOME=/sandbox "$C" openclaw config get agents.defaults.thinkingDefault
