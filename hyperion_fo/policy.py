"""Trust layer for every outbound action. Code decides who may be contacted, how often, and what is never allowed.
Used twice: by dispatch.py before it builds a Fo request, and by policy_hook.py on EVERY Fo tool call the model makes.
"""
import json, os, re, time

HERE = os.path.dirname(os.path.abspath(__file__))
CONTACTS = json.load(open(os.path.join(HERE, "contacts.json")))
LIMITS = {"ask_fo_per_incident": 5, "calls_per_incident": 2, "incidents_per_run": 3}
SPEND = re.compile(r"\b(buy|order|purchase|pay|checkout|card)\b", re.I)
PHONE = re.compile(r"\+?\d[\d\s().-]{8,}\d")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
STOP_FILE = os.path.join(HERE, "STOP")
EMAIL_ONLY = re.compile(r"^\[Hyperion[^\]]*\]\s*(Email|Two things:\s*1\. Check|Three things:\s*1\. Check)", re.I)


def digits(s): return re.sub(r"\D", "", s)
PEOPLE = CONTACTS["technicians"] + [CONTACTS["ops_lead"]]
ALLOWED_PHONES = {digits(c["phone"])[-10:] for c in PEOPLE if c.get("phone")}
ALLOWED_EMAILS = {c["email"].lower() for c in PEOPLE if c.get("email")}


def state_path(run_dir): return os.path.join(run_dir, "policy_state.json")
def load_state(run_dir):
    try: return json.load(open(state_path(run_dir)))
    except (OSError, ValueError): return {"incidents": {}, "decisions": []}
def save_state(run_dir, st): json.dump(st, open(state_path(run_dir), "w"), indent=1)


def check_message(text, run_dir, incident, allow_spend=False, record=True):
    """-> (ok, reason). Checks one natural-language instruction to Fo before it is sent."""
    st = load_state(run_dir)
    inc = st["incidents"].setdefault(incident, {"ask_fo": 0, "calls": 0, "called": []})
    reasons = []
    if os.path.exists(STOP_FILE):
        reasons.append("kill switch: STOP file present")
    if incident not in st["incidents"] or len(st["incidents"]) > LIMITS["incidents_per_run"]:
        reasons.append("too many incidents this run")
    phones = {digits(p)[-10:] for p in PHONE.findall(text) if len(digits(p)) >= 10}
    emails = {e.lower().rstrip(".") for e in EMAIL.findall(text)}
    bad = sorted((phones - ALLOWED_PHONES) | (emails - ALLOWED_EMAILS))
    if bad:
        reasons.append(f"recipient not on the allowlist: {bad}")
    verbs = re.sub(r"\bon-call\b", "", text, flags=re.I)              # "on-call technician" is not a call request
    is_call = bool(phones) or bool(re.search(r"\b(phone|call|ring|dial)\b", verbs, re.I) and
                                   not re.search(r"\bno calls?\b", verbs, re.I) and not EMAIL_ONLY.search(verbs))
    if is_call and not phones:
        reasons.append("a call request must include an approved number (no calling people by name)")
    if is_call and inc["calls"] >= LIMITS["calls_per_incident"]:
        reasons.append("call limit reached for this incident")
    if phones & set(inc.get("called", [])):
        reasons.append("this person was already called for this incident")
    if inc["ask_fo"] >= LIMITS["ask_fo_per_incident"]:
        reasons.append("Fo request limit reached for this incident")
    if SPEND.search(text) and not allow_spend:
        reasons.append("spending needs a human approval")
    ok = not reasons
    if record:
        if ok:
            inc["ask_fo"] += 1
            inc["calls"] += 1 if is_call else 0
            inc.setdefault("called", []).extend(sorted(phones))
        st["decisions"].append({"t": round(time.time(), 2), "incident": incident, "ok": ok, "reasons": reasons,
                                "recipients": sorted(phones | emails), "is_call": bool(is_call)})
        save_state(run_dir, st)
    return ok, "; ".join(reasons) or "ok"
