"""navbot: the Fleet & Field Discord bot. Receives supervisor events over HTTP,
posts them, records every request in #approvals, and sends approver-only decisions back.
Everything that happens through the bot is recorded in the activity table (store.py)."""
import asyncio
import json
import logging
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp
import discord
from aiohttp import web

import approvals
import config
import fleetlog
import formatter
import llm
import store
import telemetry

log = logging.getLogger("navbot")
TZ = ZoneInfo("America/New_York")
CHANNEL_NAMES = {cid: name for name, cid in config.CHANNELS.items()}
if config.CH_MONITOR_BOT:
    CHANNEL_NAMES[config.CH_MONITOR_BOT] = "monitor-bot"


class ChannelMissing(Exception):
    """Channel ID in .env is wrong or the bot can't see the channel."""


def clip_files(paths):
    files, total = [], 0
    limit = config.MAX_UPLOAD_MB * 1024 * 1024
    for path in (paths or [])[: config.MAX_CLIPS]:
        try:
            size = os.path.getsize(path)
        except OSError:
            log.warning("clip missing: %s", path)
            continue
        if total + size > limit:
            log.warning("clip skipped, upload limit: %s (%.1f MB)", path, size / 1e6)
            continue
        total += size
        files.append(discord.File(path))
    return files


def record(kind: str, *, channel_id=None, user=None, user_id=None, **kw) -> None:
    """One row in the activity table, plus a line in the log file."""
    if user is not None:
        kw.setdefault("user_name", getattr(user, "display_name", None) or str(user))
        user_id = user.id
    store.activity(kind, channel_id=channel_id, channel=CHANNEL_NAMES.get(channel_id),
                   user_id=user_id, **kw)
    log.info("activity %s %s", kind, {k: v for k, v in kw.items() if v is not None})


class OpsBot(discord.Client):
    def __init__(self, read_messages: bool = True):
        intents = discord.Intents.default()  # guilds + reactions
        # Needed to read requests people type in #approvals. It's a privileged intent:
        # turn on "Message Content Intent" for the bot in the Discord Developer Portal.
        intents.message_content = read_messages
        super().__init__(
            intents=intents,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=True, users=True),
        )
        self.read_messages = read_messages
        self.web_session = None
        self.runner = None
        self.last_telemetry = None  # monotonic time of the last report
        self.stale_warned = False

    # ---------- startup ----------
    async def setup_hook(self):
        self.web_session = aiohttp.ClientSession()
        app = web.Application()
        app.router.add_post("/events", self.handle_event)
        app.router.add_post("/telemetry", self.handle_telemetry)
        app.router.add_get("/health", self.handle_health)
        self.loop.create_task(self.watch_telemetry())
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        await web.TCPSite(self.runner, config.LISTEN_HOST, config.LISTEN_PORT).start()
        log.info("listening on http://%s:%s/events", config.LISTEN_HOST, config.LISTEN_PORT)

    async def on_ready(self):
        log.info("logged in as %s", self.user)
        record("bot_ready", user=self.user, reads_messages=self.read_messages)
        for name, cid in config.CHANNELS.items():
            if self.get_channel(cid) is None:
                log.error("channel %s (%s) not found or not visible to the bot", name, cid)
        if config.CH_MONITOR_BOT is None:
            log.warning("CH_MONITOR_BOT not set: /telemetry is off")
        elif self.get_channel(config.CH_MONITOR_BOT) is None:
            log.error("channel monitor-bot (%s) not found or not visible to the bot", config.CH_MONITOR_BOT)

    async def close(self):
        if self.runner:
            await self.runner.cleanup()
        if self.web_session:
            await self.web_session.close()
        await super().close()

    # ---------- inbound events ----------
    async def handle_health(self, request):
        return web.json_response({"ok": True, "discord_ready": self.is_ready()})

    def _reject(self, kind: str, error: str, status: int, **kw):
        record(kind, error=error, http_status=status, **kw)
        return web.json_response({"error": error}, status=status)

    async def handle_event(self, request):
        if config.SHARED_KEY and request.headers.get("X-Bot-Key") != config.SHARED_KEY:
            return self._reject("event_rejected", "unauthorized", 401, remote=request.remote)
        try:
            ev = await request.json()
        except Exception:
            return self._reject("event_rejected", "body must be JSON", 400)
        if not isinstance(ev, dict):
            return self._reject("event_rejected", "body must be a JSON object", 400)

        etype = ev.get("event")
        if etype not in formatter.ROUTES:
            return self._reject("event_rejected", f"unknown event {etype}", 400, event=etype)
        if etype == "APPROVAL_REQUEST":
            if not ev.get("request_id") or not ev.get("options"):
                return self._reject("event_rejected", "request_id and options required", 400, event=etype)
            if store.approval_exists(ev["request_id"]):
                return self._reject("event_rejected", "duplicate request_id", 409, event=etype,
                                    request_id=ev["request_id"])
        record("event_received", event=etype, request_id=ev.get("request_id"))

        await self.wait_until_ready()
        try:
            message = await self.post_event(ev)
        except KeyError as exc:
            return self._reject("event_failed", f"missing field {exc}", 400, event=etype)
        except ChannelMissing as exc:
            return self._reject("event_failed", f"channel not visible: {exc}", 500, event=etype)
        except discord.HTTPException as exc:
            log.exception("Discord rejected the post")
            return self._reject("event_failed", f"discord error: {exc}", 502, event=etype)
        return web.json_response({"ok": True, "message_id": str(message.id)})

    async def post_event(self, ev: dict) -> discord.Message:
        channel = self.get_channel(config.CHANNELS[formatter.ROUTES[ev["event"]]])
        if channel is None:
            raise ChannelMissing(formatter.ROUTES[ev["event"]])
        if ev["event"] in formatter.RED_ROOM_TYPES:
            # #red-room is card only: the model's text goes in the Debrief field, not above the card.
            formatter.build(ev)  # validates fields before spending time on the LLM
            debrief = ev.get("debrief") or await llm.debrief(self.web_session, ev)
            embed = formatter.build(ev, debrief=debrief)
            headline = None
        else:
            embed = formatter.build(ev)  # validates fields before anything is posted
            card_only = formatter.ROUTES[ev["event"]] in formatter.CARD_ONLY
            headline = None if card_only else await llm.headline(self.web_session, ev)
        if ev["event"] == "APPROVAL_REQUEST":
            approvals.set_status(embed, "⏳ Pending")
        content = " ".join(p for p in (formatter.mentions(ev), headline) if p) or None

        kwargs = {"content": content, "embed": embed}
        files = clip_files(ev.get("clips"))
        if files:
            kwargs["files"] = files
        message = await channel.send(**kwargs)
        store.log_event(ev, message.id)
        record("card_posted", channel_id=channel.id, message_id=message.id, event=ev["event"],
               request_id=ev.get("request_id"), headline=headline)

        if ev["event"] == "APPROVAL_REQUEST":
            context = ev.get("context") or []
            store.add_approval(
                ev["request_id"], message.id, channel.id, ev["options"], source="supervisor",
                kind=ev.get("kind"), summary=context[-1] if context else ev.get("kind"),
                details="\n".join(context), requested_by=ev.get("requested_by") or "supervisor",
            )
            record("request_created", channel_id=channel.id, message_id=message.id,
                   request_id=ev["request_id"], source="supervisor", type=ev.get("kind"))
            for option in ev["options"]:
                await message.add_reaction(option["emoji"])
        return message

    # ---------- hourly telemetry (#monitor-bot) ----------
    async def handle_telemetry(self, request):
        if config.SHARED_KEY and request.headers.get("X-Bot-Key") != config.SHARED_KEY:
            return self._reject("telemetry_rejected", "unauthorized", 401, remote=request.remote)
        text = await request.text()
        now = datetime.now(TZ)
        fleet = None  # the parsed JSON fleet log, when the supervisor sends one instead of a CSV
        try:
            if fleetlog.is_fleet_log(text):
                fleet = fleetlog.parse(text)
                checked = fleetlog.assess(fleet)
                embed = fleetlog.build(fleet, checked, now)
            else:
                checked = telemetry.assess(text)
                embed = telemetry.build(checked, now)
        except (telemetry.TelemetryError, fleetlog.FleetLogError) as exc:
            return self._reject("telemetry_rejected", str(exc), 400)
        except Exception as exc:  # malformed CSV or a log missing expected fields
            return self._reject("telemetry_rejected", f"bad telemetry: {exc!r}", 400)

        await self.wait_until_ready()
        channel = self.get_channel(config.CH_MONITOR_BOT) if config.CH_MONITOR_BOT else None
        if channel is None:
            return self._reject("telemetry_failed", "channel not visible: monitor-bot (set CH_MONITOR_BOT)", 500)
        try:
            message = await channel.send(embed=embed)  # card only; the raw data stays in data/bot.db
        except discord.HTTPException as exc:
            log.exception("Discord rejected the telemetry post")
            return self._reject("telemetry_failed", f"discord error: {exc}", 502)

        self.last_telemetry, self.stale_warned = time.monotonic(), False
        # Robot monitor log. For a fleet log, keep meta + summary in the report row; events and
        # snapshots go into their own tables rather than one large text blob.
        raw = json.dumps({k: fleet[k] for k in ("meta", "summary") if k in fleet}) if fleet else text
        report_id = store.add_telemetry(message.id, checked, raw)
        logged = {}
        if fleet:
            n_events, n_snaps = store.add_fleet_log(report_id, fleet, fleetlog.robot_name,
                                                    fleetlog.Clock(fleet.get("meta", {})))
            logged = {"events_logged": n_events, "snapshot_rows_logged": n_snaps,
                      "incidents": len(fleetlog.incidents(fleet))}
        store.log_event({"event": "TELEMETRY", "format": "fleet_log" if fleet else "csv",
                         "report_id": report_id}, message.id)
        record("telemetry_posted", channel_id=channel.id, message_id=message.id, report_id=report_id,
               format="fleet_log" if fleet else "csv", robots=len(checked),
               down=[r["robot"] for r, h, _ in checked if h == "DOWN"],
               warn=[r["robot"] for r, h, _ in checked if h == "WARN"], **logged)
        return web.json_response({"ok": True, "message_id": str(message.id), "report_id": report_id, **logged})

    async def watch_telemetry(self):
        """Warns once in #monitor-bot when the hourly report is late."""
        await self.wait_until_ready()
        started = time.monotonic()
        while not self.is_closed():
            await asyncio.sleep(60)
            if config.CH_MONITOR_BOT is None or self.stale_warned:
                continue
            since = self.last_telemetry or started
            if time.monotonic() - since < config.TELEMETRY_STALE_MIN * 60:
                continue
            channel = self.get_channel(config.CH_MONITOR_BOT)
            if channel:
                what = "since the last report" if self.last_telemetry else "since navbot started"
                message = await channel.send(embed=discord.Embed(
                    title="⚠️ Telemetry report is late",
                    description=f"No telemetry from the supervisor for {config.TELEMETRY_STALE_MIN:.0f}+ minutes "
                                f"{what}. Check that the supervisor is running.",
                    color=formatter.YELLOW,
                ).set_author(name=formatter.FLEET))
                record("telemetry_late", channel_id=channel.id, message_id=message.id)
                self.stale_warned = True

    # ---------- messages ----------
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.channel.id not in CHANNEL_NAMES:
            return
        record("message", channel_id=message.channel.id, user=message.author, message_id=message.id,
               content=message.content[:2000], attachments=len(message.attachments),
               reply_to=message.reference.message_id if message.reference else None)
        # A new message in #approvals is a request. Replies are conversation about a request, not new ones.
        if message.channel.id == config.CHANNELS["approvals"] and not message.reference and message.content.strip():
            await self.create_request(message)

    async def create_request(self, message: discord.Message):
        text = message.content.strip()
        kind = approvals.classify(text)
        request_id = store.next_request_id()
        card = await message.reply(
            content=f"<@&{config.ROLE_APPROVER}>",
            embed=approvals.request_card(request_id, kind, text, message.author),
            mention_author=False,
        )
        store.add_approval(
            request_id, card.id, message.channel.id, approvals.OPTIONS, source="discord",
            kind=kind, summary=approvals.summarize(text), details=text,
            requested_by=message.author.display_name, requested_by_id=message.author.id,
            request_message_id=message.id,
        )
        record("request_created", channel_id=message.channel.id, user=message.author, message_id=card.id,
               request_id=request_id, source="discord", type=kind, summary=approvals.summarize(text))
        for option in approvals.OPTIONS:
            await card.add_reaction(option["emoji"])

    # ---------- approvals ----------
    async def on_raw_reaction_remove(self, p: discord.RawReactionActionEvent):
        if p.user_id == self.user.id or p.channel_id not in CHANNEL_NAMES:
            return
        req = store.get_by_message(p.message_id)
        record("reaction_removed", channel_id=p.channel_id, user_id=p.user_id, message_id=p.message_id,
               request_id=req["request_id"] if req else None, emoji=str(p.emoji))

    async def on_raw_reaction_add(self, p: discord.RawReactionActionEvent):
        if p.user_id == self.user.id or p.channel_id not in CHANNEL_NAMES:
            return
        emoji = str(p.emoji)
        req = store.get_by_message(p.message_id)
        record("reaction_added", channel_id=p.channel_id, user=p.member, user_id=p.user_id,
               message_id=p.message_id, request_id=req["request_id"] if req else None, emoji=emoji)
        if req is None:  # not an approval card: nothing to decide
            return

        channel = self.get_channel(p.channel_id)
        if channel is None:
            return
        try:
            message = await channel.fetch_message(p.message_id)
        except discord.HTTPException:
            return

        option = next((o for o in req["options"] if o["emoji"] == emoji), None)
        is_approver = p.member is not None and any(r.id == config.ROLE_APPROVER for r in p.member.roles)
        why = ("not one of the options" if option is None
               else "not an approver" if not is_approver
               else f"already {req['status']}" if req["status"] != "pending"
               else None)
        if why:
            record("reaction_rejected", channel_id=p.channel_id, user=p.member, user_id=p.user_id,
                   message_id=p.message_id, request_id=req["request_id"], emoji=emoji, reason=why)
            await self._remove_reaction(message, p)
            return

        # The bot's own reaction is 1, so a count of 2 means an approver picked this option.
        count = next((r.count for r in message.reactions if str(r.emoji) == emoji), 0)
        if count < approvals.DECIDING_COUNT:
            record("reaction_counted", channel_id=p.channel_id, message_id=p.message_id,
                   request_id=req["request_id"], emoji=emoji, count=count, needed=approvals.DECIDING_COUNT)
            return

        status = approvals.status_for(emoji)
        if not store.decide(req["request_id"], status, option["choice"], option["label"],
                            p.user_id, p.member.display_name):
            await self._remove_reaction(message, p)  # someone else decided first
            return
        record("request_decided", channel_id=p.channel_id, user=p.member, message_id=p.message_id,
               request_id=req["request_id"], status=status, choice=option["choice"], count=count)
        await self.finish_decision(req, option, status, message, p)

    async def finish_decision(self, req, option, status, message, p):
        now = datetime.now(TZ)
        emoji = option["emoji"]
        note = f"{emoji} {status.capitalize()} · {option['label']} · by {p.member.mention} at {now:%H:%M}"

        delivered = True
        if req["source"] == "supervisor":  # the supervisor asked, so it gets the answer
            decision = {
                "event": "APPROVAL_DECISION",
                "request_id": req["request_id"],
                "choice": option["choice"],
                "status": status,
                "approved_by": p.member.display_name,
                "approved_by_id": str(p.user_id),
                "time": now.isoformat(timespec="seconds"),
            }
            delivered = await self.send_decision(decision)
            store.mark_delivered(req["request_id"], delivered)
            store.log_event(decision, message.id)
            record("decision_delivered" if delivered else "decision_not_delivered",
                   request_id=req["request_id"], url=config.SUPERVISOR_DECISION_URL)
            if not delivered:
                note += " · ⚠️ not delivered to supervisor"

        embed = message.embeds[0].copy() if message.embeds else discord.Embed()
        embed.color = formatter.RED if status == "denied" or not delivered else formatter.GREEN
        await message.edit(embed=approvals.set_status(embed, note))

        if req["source"] == "discord" and req.get("request_message_id"):
            await self.tell_requester(req, status, p.member)
        if not delivered:
            await self.ops_log(f"Decision {req['request_id']} could not reach {config.SUPERVISOR_DECISION_URL}")

    async def tell_requester(self, req, status, approver: discord.Member):
        channel = self.get_channel(req["channel_id"])
        if channel is None:
            return
        kind = approvals.KIND_NAMES.get(req["kind"], req["kind"]).lower()
        text = (f"<@{req['requested_by_id']}> your {kind} request {req['request_id']} was "
                f"**{status}** by {approver.display_name}.")
        try:
            original = await channel.fetch_message(req["request_message_id"])
            reply = await original.reply(text, mention_author=True)
        except discord.HTTPException:  # the original message was deleted
            reply = await channel.send(text)
        record("requester_notified", channel_id=channel.id, message_id=reply.id,
               request_id=req["request_id"], status=status)
        await self.dm_requester(req, status, approver, kind, channel)

    async def dm_requester(self, req, status, approver: discord.Member, kind: str, channel):
        """Private copy of the decision. Fails if the user blocks DMs from server members;
        the channel reply above still tells them."""
        icon = "✅" if status == "approved" else "❌" if status == "denied" else "📋"
        card = f"https://discord.com/channels/{channel.guild.id}/{channel.id}/{req['message_id']}"
        text = (f"{icon} Your {kind} request **{req['request_id']}** was **{status}** by "
                f"{approver.display_name}.\n> {req.get('summary') or ''}\n{card}")
        try:
            user = self.get_user(req["requested_by_id"]) or await self.fetch_user(req["requested_by_id"])
            dm = await user.send(text[:2000])
        except discord.HTTPException as exc:
            log.warning("could not DM requester %s: %s", req["requested_by_id"], exc)
            record("requester_dm_failed", request_id=req["request_id"], user_id=req["requested_by_id"],
                   error=str(exc)[:200])
            return
        record("requester_dm_sent", message_id=dm.id, request_id=req["request_id"],
               user_id=req["requested_by_id"], status=status)

    async def _remove_reaction(self, message, p):
        try:
            await message.remove_reaction(p.emoji, p.member or discord.Object(id=p.user_id))
        except discord.HTTPException as exc:
            log.warning("could not remove reaction (needs Manage Messages): %s", exc)

    async def send_decision(self, decision: dict) -> bool:
        headers = {"X-Bot-Key": config.SHARED_KEY} if config.SHARED_KEY else {}
        for attempt in range(3):
            try:
                async with self.web_session.post(
                    config.SUPERVISOR_DECISION_URL,
                    json=decision,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status < 300:
                        return True
                    log.warning("supervisor returned %s", resp.status)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                log.warning("decision delivery failed (attempt %d): %s", attempt + 1, exc)
            await asyncio.sleep(1 + attempt)
        return False

    async def ops_log(self, text: str):
        channel = self.get_channel(config.CHANNELS["ops-log"])
        if channel:
            message = await channel.send(f"⚙️ {text}"[:2000])
            record("ops_log_posted", channel_id=channel.id, message_id=message.id, text=text)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        OpsBot().run(config.DISCORD_TOKEN, log_handler=None)
    except discord.PrivilegedIntentsRequired:
        log.error("Message Content Intent is off in the Discord Developer Portal (Bot tab). Running without "
                  "it: supervisor approvals still work, but requests typed in #approvals can't be read.")
        OpsBot(read_messages=False).run(config.DISCORD_TOKEN, log_handler=None)
