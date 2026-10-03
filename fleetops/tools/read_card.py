import json, sys, urllib.request
env = dict(l.split("=", 1) for l in open("/home/dell/navfix/navbot/.env").read().splitlines() if "=" in l and not l.startswith("#"))
ch, mid = env[sys.argv[1]], sys.argv[2]
req = urllib.request.Request(f"https://discord.com/api/v10/channels/{ch}/messages/{mid}",
                             headers={"Authorization": f"Bot {env['DISCORD_TOKEN']}", "User-Agent": "fleetops-check (local, 1.0)"})
m = json.load(urllib.request.urlopen(req, timeout=15))
if m.get("content"): print("content:", m["content"][:300])
for e in m.get("embeds", []):
    print("TITLE:", e.get("title"))
    if e.get("description"): print("  description:", e["description"][:300])
    for f in e.get("fields", []):
        print(f"  {f['name']}: {f['value'][:340]}")
