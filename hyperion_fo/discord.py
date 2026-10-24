"""Team Discord updates for each incident, through a channel webhook (contacts.json "discord_webhook").
Posted by code, not the model: fixed templates, only to the one webhook on the allowlist, failures logged."""
import json, os, urllib.request
import policy

URL = policy.CONTACTS.get("discord_webhook") or os.environ.get("HYPERION_DISCORD_WEBHOOK", "")


def post(text, emit=None, dry=False, incident=None):
    if not URL:
        if emit: emit("discord", "skipped", summary="no webhook configured", incident=incident)
        return False
    if dry:
        if emit: emit("discord", "dry_run", summary=text[:160], incident=incident)
        return True
    req = urllib.request.Request(URL, data=json.dumps({"content": text[:1900], "username": "Hyperion Ops"}).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": "hyperion-fo/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            ok = 200 <= r.status < 300
    except OSError as e:
        ok = False
        if emit: emit("discord", "failed", summary=str(e)[:160], incident=incident)
    if ok and emit: emit("discord", "posted", summary=text[:160], incident=incident)
    return ok
