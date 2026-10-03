"""Mock adapters: one per source, all reading the shared in-process MockWorld."""
import random

from .base import CalendarSource, CellSource, ClockSource, MessageSource, SystemSource, TicketSource


class MockTickets(TicketSource):
    def __init__(self, world):
        self.w = world

    def read(self):
        st = self.w.state()
        return {"needs": st["needs"], "visit_events": st["visit_events"]}

    def fire_need(self, need_id):
        self.w.fire(need_id)

    def reset(self):
        self.w.reset()


class MockCalendar(CalendarSource):
    def __init__(self, world):
        self.w = world

    def read(self):
        st = self.w.state()
        return {k: st[k] for k in ("staff", "events", "legs", "jobs", "changes", "prefs")}

    def inject_delay(self, line, minutes):
        self.w.inject_delay(line, minutes)

    def update_event(self, event_id, fields, reason=None):
        self.w.edit_event(event_id, fields, reason)

    def create_event(self, staff_id, fields, reason=None):
        self.w.create_event(staff_id, fields, reason)

    def revert_change(self, change_id):
        self.w.revert_change(change_id)

    def reset(self):
        self.w.reset()


class MockCell(CellSource):
    def __init__(self, world):
        self.w = world

    def read(self):
        st = self.w.state()
        return {k: st[k] for k in ("reliability", "commissioning", "warehouse")}

    def clip_path(self, name):
        # No clips ship with the mock; drop MP4s into fixtures/clips/ to see them here.
        root = (self.w.fixtures_dir / "clips").resolve()
        target = (root / name).resolve()
        return str(target) if root in target.parents and target.is_file() else None

    def reset(self):
        self.w.reset()


class MockClock(ClockSource):
    def __init__(self, world):
        self.w = world

    def read(self):
        return {"sim_time": self.w.clock.now(), "speed": self.w.clock.speed}

    def set(self, sim_time=None, speed=None):
        self.w.clock.set(sim_time, speed)

    def reset(self):
        self.w.reset()


class MockMessages(MessageSource):
    def __init__(self, world):
        self.w = world

    def read(self):
        return {"messages": self.w.state()["messages"]}


class MockSystem(SystemSource):
    def __init__(self, cfg):
        self.cfg = cfg

    def read(self):
        services = [{"name": s["name"], "port": s.get("port"), "up": True, "detail": "mock"}
                    for s in self.cfg["services"]]
        return {
            "vllm": {"up": True, "model": self.cfg.get("expected_model"), "detail": "mock"},
            "gpu": {"used_gb": round(96.4 + random.uniform(-0.4, 0.4), 1),
                    "total_gb": self.cfg.get("gpu_total_gb", 128), "source": "mock"},
            "services": services,
            "cloud_model_calls": 0,
            "egress_source": "mock",
        }
