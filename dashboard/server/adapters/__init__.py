"""Builds one adapter per source, mock or live, from config."""
from pathlib import Path

from . import live, mock, navbot
from .mock_world import MockWorld

SOURCES = ("tickets", "calendar", "cell", "clock", "messages", "system", "feeds")


def source_modes(cfg):
    default = cfg.get("mode", "mock")
    overrides = cfg.get("sources") or {}
    return {s: (overrides.get(s) or default) for s in SOURCES}


def build(cfg, root):
    modes = source_modes(cfg)
    demo, lv = cfg["demo"], cfg.get("live", {})
    world = None
    if "mock" in modes.values():
        world = MockWorld(Path(root) / "fixtures", demo)

    db = navbot.NavbotDB(lv.get("navbot", {"db_path": "../navbot/data/bot.db"})) if "navbot" in modes.values() else None
    from_navbot = {"tickets": lambda: navbot.NavbotTickets(db), "cell": lambda: navbot.NavbotCell(db),
                   "messages": lambda: navbot.NavbotMessages(db)}

    def pick(name, make_mock, make_live):
        if modes[name] == "navbot":
            if name not in from_navbot:
                raise ValueError(f"source {name!r} can't come from navbot (only {', '.join(from_navbot)})")
            return from_navbot[name]()
        return make_mock() if modes[name] == "mock" else make_live()

    adapters = {
        "tickets": pick("tickets", lambda: mock.MockTickets(world), lambda: live.LiveTickets(lv["tickets"])),
        "calendar": pick("calendar", lambda: mock.MockCalendar(world),
                         lambda: live.LiveCalendar(lv["calendar"], lv.get("field", {}), demo["date"])),
        "cell": pick("cell", lambda: mock.MockCell(world), lambda: live.LiveCell(lv["cell"])),
        "clock": pick("clock", lambda: mock.MockClock(world), lambda: live.LiveClock(lv["clock"], demo["start_time"])),
        "messages": pick("messages", lambda: mock.MockMessages(world), lambda: live.LiveMessages(lv["messages"])),
        "system": pick("system", lambda: mock.MockSystem(lv["system"]), lambda: live.LiveSystem(lv["system"])),
        "feeds": pick("feeds", lambda: mock.MockFeeds(world), lambda: live.LiveFeeds(lv.get("feeds", {}), lv["cell"])),
    }
    return adapters, modes
