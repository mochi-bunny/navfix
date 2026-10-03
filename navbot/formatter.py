"""Turns supervisor events into Discord embeds. Every fact comes from the event."""
import discord

import config

ROUTES = {
    "SKILL_REFUSED": "red-room",
    "TASK_FAILED": "red-room",
    "INFRA_ERROR": "ops-log",
    "GATE_OPEN": "dispatch-recall",
    "JOB_DISPATCHED": "dispatch-recall",
    "JOB_COMPLETED": "dispatch-recall",
    "DAILY_SUMMARY": "daily-checkin",
    "DAILY_RECAP": "daily-checkin",
    "APPROVAL_REQUEST": "approvals",
    "ETA_UPDATE": "cell-globex",
}

FLEET, FIELD = "FLEET · robots", "FIELD · people"
AGENT = {
    "SKILL_REFUSED": FLEET, "TASK_FAILED": FLEET, "INFRA_ERROR": FLEET,
    "GATE_OPEN": FLEET, "JOB_DISPATCHED": FLEET, "JOB_COMPLETED": FLEET,
    "ETA_UPDATE": FLEET,
    "DAILY_SUMMARY": FIELD, "DAILY_RECAP": FIELD, "APPROVAL_REQUEST": FIELD,
}

RED, GREEN, YELLOW, BLUE, GREY = 0xE5484D, 0x30A46C, 0xF5A524, 0x3E63DD, 0x8B8D98


def _v(text) -> str:
    """Embed field values max out at 1024 characters."""
    text = str(text) if text not in (None, "") else "—"
    return text[:1024]


def _list(items) -> str:
    return _v("\n".join(str(i) for i in items) if items else "")


def _skill_name(skill) -> str:
    """open_middle_drawer -> Open middle drawer"""
    return str(skill).replace("_", " ").strip().capitalize()


def _bar(passed, run) -> str:
    """10-block pass/fail bar, e.g. 🟩🟥🟥🟥🟥🟥🟥🟥🟥🟥 for 1 of 10."""
    try:
        passed, run = int(passed), int(run)
    except (TypeError, ValueError):
        return ""
    if run <= 0:
        return ""
    green = round(10 * max(0, min(passed, run)) / run)
    return "🟩" * green + "🟥" * (10 - green)


def _pct(passed, run) -> str:
    try:
        return f" ({round(100 * int(passed) / int(run))}%)"
    except (TypeError, ValueError, ZeroDivisionError):
        return ""


def _robot_location(e: discord.Embed, ev: dict) -> None:
    """🤖 Robot and 📍 Location side by side, shared by #red-room and #dispatch-recall cards."""
    location = f"{ev['site']}\ncell {ev['cell']}" if ev.get("cell") else ev["site"]
    e.add_field(name="🤖 Robot", value=_v(f"`{ev['robot']}`" if ev.get("robot") else "Not reported"))
    e.add_field(name="📍 Location", value=_v(location))


def _order(ev: dict) -> str:
    order = f"**{ev['order_id']}**"
    return order + (f"\nticket {ev['ticket_id']}" if ev.get("ticket_id") else "")


def _plan(skills, mark: str) -> str:
    """Numbered skill list: '▫️ 1. Open middle drawer · `open_middle_drawer`'"""
    lines = [f"{mark} {i}. {_skill_name(s)} · `{s}`" for i, s in enumerate(skills, 1)]
    return _v("\n".join(lines))


# Channels that get the card only, with no text line above it.
CARD_ONLY = {"red-room", "dispatch-recall", "approvals"}

RED_ROOM_TYPES = {"SKILL_REFUSED": "Skill refused", "TASK_FAILED": "Task failed"}


def default_debrief(ev: dict) -> str:
    """Plain debrief built only from event fields, used when neither the supervisor nor Qwen gives one."""
    site, skill = ev["site"], _skill_name(ev["skill"]).lower()
    robot = f"Robot {ev['robot']}" if ev.get("robot") else "The robot"
    passed, run = ev["passed"], ev["run"]
    if ev["event"] == "SKILL_REFUSED":
        text = (
            f"{robot} was tested on '{skill}' at {site} and passed {passed} of {run} runs. "
            f"That's below the safety bar, so the skill is blocked at this site until an engineer fixes it."
        )
    else:
        text = (
            f"{robot} failed '{skill}' at {site} while running a job, with {passed} of {run} attempts succeeding. "
            f"The job needs an engineer to look at it."
        )
    if ev.get("failure_note"):
        text += f" Observed: {ev['failure_note']}"
    return text


def build(ev: dict, debrief: str | None = None) -> discord.Embed:
    """Raises KeyError if a required field is missing (the server returns 400).
    `debrief` is only used for #red-room cards."""
    t = ev["event"]

    if t in RED_ROOM_TYPES:
        skill, site = _skill_name(ev["skill"]), ev["site"]
        passed, run = ev["passed"], ev["run"]
        gate = ev.get("gate") or "≥80% success at 95% confidence"
        icon = "⛔" if t == "SKILL_REFUSED" else "❌"
        e = discord.Embed(title=f"{icon} {RED_ROOM_TYPES[t].upper()} · {skill}", color=RED)

        code = f"`{ev['error_code']}`\n{RED_ROOM_TYPES[t]}" if ev.get("error_code") else f"`{t}`"
        _robot_location(e, ev)
        e.add_field(name="🏷️ Error code", value=_v(code))

        if ev.get("failure_note"):
            issue = ev["failure_note"]
        elif t == "SKILL_REFUSED":
            issue = f"Passed only {passed} of {run} test runs{_pct(passed, run)}, below the safety bar."
        else:
            issue = f"Failed during a job: {passed} of {run} attempts succeeded{_pct(passed, run)}."
        e.add_field(name="⚠️ Issue", value=_v(f"**{issue}**"), inline=False)
        e.add_field(name="📝 Debrief", value=_v(debrief or default_debrief(ev)), inline=False)

        if t == "SKILL_REFUSED":
            e.add_field(name="Test results", value=_v(f"{_bar(passed, run)}\n{passed} passed · {run} tried"))
            e.add_field(name="Safety bar", value=_v(gate))
        else:
            e.add_field(name="Attempts in this job", value=_v(f"{_bar(passed, run)}\n{passed} succeeded · {run} tried"))
            if ev.get("gate"):
                e.add_field(name="Safety bar", value=_v(ev["gate"]))

        if ev.get("ticket_id") and ev.get("deadline"):
            next_step = f"🧰 Field engineer needed by **{ev['deadline']}** · ticket **{ev['ticket_id']}**"
        elif ev.get("ticket_id"):
            next_step = f"🧰 Field engineer needed · ticket **{ev['ticket_id']}**"
        elif ev.get("deadline"):
            next_step = f"🧰 Field engineer needed by **{ev['deadline']}** · no ticket yet"
        else:
            next_step = "🧰 Needs a field engineer to look at it · no ticket yet"
        e.add_field(name="What happens next", value=_v(next_step), inline=False)
        e.set_footer(text=f"skill: {ev['skill']}")

    elif t == "INFRA_ERROR":
        e = discord.Embed(
            title=f"⚙️ INFRA · {ev['source']}", description=str(ev["message"])[:4000], color=GREY
        )

    elif t == "GATE_OPEN":
        skill, site = _skill_name(ev["skill"]), ev["site"]
        passed, run = ev["passed"], ev["run"]
        e = discord.Embed(
            title=f"🟢 GATE OPEN · {skill}",
            description=f"The robot is now **cleared to do this task at {site}**. "
                        f"It passed the safety bar, so orders that use this skill can be dispatched.",
            color=GREEN,
        )
        _robot_location(e, ev)
        e.add_field(name="🎫 Ticket", value=_v(ev.get("ticket_id") or "None"))
        e.add_field(name="🧠 Winning policy", value=_v(f"Policy **{ev['policy']}**"))
        e.add_field(name="✅ Result", value=_v(f"{_bar(passed, run)}\n{passed} of {run} passed"))
        e.add_field(name="📊 Confidence", value=_v(f"**{ev['confidence_pct']}%** sure success is ≥80%"))
        testing = f"{ev['total_trials']} trials in total"
        try:
            saved = int(ev["baseline_trials"]) - int(ev["total_trials"])
            testing += f" · a fixed sweep would need ~{ev['baseline_trials']}"
            if saved > 0:
                testing += f" (**{saved} fewer trials**)"
        except (KeyError, TypeError, ValueError):
            pass
        e.add_field(name="🔬 Testing", value=_v(testing), inline=False)
        e.add_field(name="What happens next", value="🚚 Orders that use this skill can now go to this site",
                    inline=False)
        e.set_footer(text=f"skill: {ev['skill']}")
        e.timestamp = discord.utils.utcnow()

    elif t == "JOB_DISPATCHED":
        skills = ev["skills"]
        e = discord.Embed(
            title=f"🟡 DISPATCHED · Order {ev['order_id']}",
            description=f"Order **{ev['order_id']}** was sent to the robot at **{ev['site']}**. "
                        f"It will run **{len(skills)} skill{'s' if len(skills) != 1 else ''}** in this order.",
            color=YELLOW,
        )
        _robot_location(e, ev)
        e.add_field(name="📦 Order", value=_v(_order(ev)))
        e.add_field(name="📋 Plan", value=_plan(skills, "▫️"), inline=False)
        e.add_field(name="Status", value="▶️ Running now. A completed card follows when it's done", inline=False)
        e.timestamp = discord.utils.utcnow()

    elif t == "JOB_COMPLETED":
        skills = ev["skills"]
        minutes, seconds = divmod(int(ev["duration_s"]), 60)
        took = f"{minutes}m {seconds:02d}s"
        e = discord.Embed(
            title=f"✅ COMPLETED · Order {ev['order_id']}",
            description=f"The robot at **{ev['site']}** finished order **{ev['order_id']}** in **{took}**.",
            color=GREEN,
        )
        _robot_location(e, ev)
        e.add_field(name="📦 Order", value=_v(_order(ev)))
        e.add_field(name="✔️ Skills done", value=_plan(skills, "✅"), inline=False)
        e.add_field(name="⏱️ Time taken", value=took)
        e.add_field(name="🔢 Skills", value=f"{len(skills)} of {len(skills)}")
        e.timestamp = discord.utils.utcnow()

    elif t in ("DAILY_SUMMARY", "DAILY_RECAP"):
        icon = "☀️" if t == "DAILY_SUMMARY" else "🌙"
        counts = ", ".join(f"{n} {kind}" for kind, n in ev.get("counts", {}).items())
        e = discord.Embed(title=f"{icon} {ev['date']}" + (f" · {counts}" if counts else ""), color=BLUE)
        rows = []
        for x in ev.get("events", []):
            row = f"**{x['time']}** {x['title']} · {x['place']}"
            if x.get("leave_by"):
                row += f" · leave {x['leave_by']}"
            if x.get("mode"):
                row += f" ({x['mode']}" + (f", ~{x['minutes']} min)" if x.get("minutes") else ")")
            rows.append(row)
        if rows:
            e.description = "\n".join(rows)[:4000]
        if ev.get("tightest"):
            e.add_field(name="Tightest connection", value=_v(ev["tightest"]), inline=False)
        if ev.get("free"):
            e.add_field(name="Free", value=_v(", ".join(ev["free"])), inline=False)
        if ev.get("weather"):
            e.add_field(name="Weather", value=_v(ev["weather"]), inline=False)
        if ev.get("fleet"):
            e.add_field(name="Fleet", value=_list(ev["fleet"]), inline=False)
        if ev.get("moved"):
            e.add_field(name="Moved today", value=_list(ev["moved"]), inline=False)
        if ev.get("shipped"):
            e.add_field(name="Shipped today", value=_list(ev["shipped"]), inline=False)

    elif t == "APPROVAL_REQUEST":
        title = f"📋 APPROVAL · {ev['kind']}"
        if ev.get("ticket_id"):
            title += f" · {ev['ticket_id']}"
        e = discord.Embed(title=title, description=_list(ev.get("context", []))[:4000], color=YELLOW)
        options = "   ".join(f"{o['emoji']} {o['label']}" for o in ev["options"])
        e.add_field(name="Options", value=_v(options), inline=False)

    elif t == "ETA_UPDATE":
        title = f"🕒 ETA UPDATE · {ev['site']}"
        if ev.get("ticket_id"):
            title += f" · {ev['ticket_id']}"
        text = f"{ev['engineer']} now arriving around {ev['eta']}."
        if ev.get("reason"):
            text += f" Reason: {ev['reason']}"
        e = discord.Embed(title=title, description=text, color=BLUE)

    else:
        raise KeyError(f"no template for {t}")

    e.set_author(name=AGENT[t])
    return e


def mentions(ev: dict) -> str:
    t = ev["event"]
    if t in ("SKILL_REFUSED", "TASK_FAILED"):
        ids = [config.ROLE_FIELD_ENGINEER, config.ROLE_CUSTOMER]
    elif t == "APPROVAL_REQUEST":
        ids = [config.ROLE_APPROVER]
    elif t == "ETA_UPDATE":
        ids = [config.ROLE_CUSTOMER]
    else:
        ids = []
    return " ".join(f"<@&{i}>" for i in ids if i)
