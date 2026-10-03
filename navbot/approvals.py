"""Requests people post in #approvals: the request type (worked out in code, not by the model),
the request card, and the status line every approval card shows."""
import re

import discord

import formatter

APPROVE, DENY = "✅", "❌"
OPTIONS = [{"emoji": APPROVE, "choice": "approve", "label": "Approve"},
           {"emoji": DENY, "choice": "deny", "label": "Deny"}]

# A reaction count of 2 decides: the bot's own reaction plus one approver.
DECIDING_COUNT = 2

# First match wins, so the more specific types come first.
KINDS = [
    ("TIME_OFF", "Time off",
     r"\b(time off|\w*days? off|take \w+ off|leave|vacation|pto|sick|holiday|out of office|ooo)\b"),
    ("SCHEDULE_CHANGE", "Schedule change",
     r"\b(schedul\w*|reschedul\w*|shift|swap|roster|move (my|the)|push (my|the)|earlier|later|start time|end time)\b"),
    ("SITE_VISIT", "Site visit", r"\b(site visit|visit|on ?site|commission\w*)\b"),
    ("TRAVEL", "Travel", r"\b(travel|trip|flight|taxi|uber|lyft|hotel|mileage|train)\b"),
    ("PURCHASE", "Purchase / expense",
     r"\b(buy|purchase|order|expense|reimburs\w*|budget|invoice|spare|part|parts|tool|tools)\b"),
    ("ACCESS", "Access", r"\b(access|permission|account|badge|login|key|keys)\b"),
]
KIND_NAMES = {k: name for k, name, _ in KINDS} | {"OTHER": "Other", "BOOK_VISIT": "Book visit", "REPLAN": "Re-plan"}


def classify(text: str) -> str:
    low = text.lower()
    return next((kind for kind, _, pattern in KINDS if re.search(pattern, low)), "OTHER")


def summarize(text: str, limit: int = 120) -> str:
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return first if len(first) <= limit else first[: limit - 1] + "…"


def status_for(emoji: str) -> str:
    return {APPROVE: "approved", DENY: "denied"}.get(emoji, "decided")


def request_card(request_id: str, kind: str, text: str, requester: discord.abc.User) -> discord.Embed:
    e = discord.Embed(title=f"📋 REQUEST · {KIND_NAMES.get(kind, kind)} · {request_id}",
                      description=text[:4000], color=formatter.YELLOW)
    e.set_author(name=formatter.FIELD)
    e.add_field(name="Requested by", value=requester.mention, inline=True)
    e.add_field(name="Type", value=KIND_NAMES.get(kind, kind), inline=True)
    e.add_field(name="Options",
                value="   ".join(f"{o['emoji']} {o['label']}" for o in OPTIONS), inline=False)
    e.add_field(name="Status", value="⏳ Pending", inline=False)
    return e


def set_status(embed: discord.Embed, value: str) -> discord.Embed:
    """Replaces the card's Status field, or adds one if the card has none."""
    for i, field in enumerate(embed.fields):
        if field.name == "Status":
            embed.set_field_at(i, name="Status", value=value, inline=False)
            return embed
    embed.add_field(name="Status", value=value, inline=False)
    return embed
