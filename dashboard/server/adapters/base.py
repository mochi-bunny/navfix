"""Adapter contracts. One adapter per data source; mock and live both implement these.

Every read() returns plain dicts. Datetimes may be aware datetimes or ISO strings;
the snapshot layer normalises them. Field names are documented in INTERFACES.md.
Control methods raise NotImplementedError when a source has no such endpoint.
"""


class Source:
    def read(self):
        raise NotImplementedError

    def reset(self):
        raise NotImplementedError


class TicketSource(Source):
    """-> {"needs": [Need], "visit_events": [VisitEvent]}"""

    def fire_need(self, need_id):
        raise NotImplementedError


class CalendarSource(Source):
    """-> {"staff": [...], "events": [...], "legs": [...], "jobs": [...], "changes": [...], "prefs": {...}}"""

    def inject_delay(self, line, minutes):
        raise NotImplementedError

    def update_event(self, event_id, fields, reason=None):
        """Ask Field to change an event (title/place/start/end). Field re-checks travel and notifies."""
        raise NotImplementedError

    def create_event(self, staff_id, fields, reason=None):
        """Ask Field to add an event (title/type/place/start/end). Field checks conflicts and travel."""
        raise NotImplementedError

    def revert_change(self, change_id):
        raise NotImplementedError


class CellSource(Source):
    """-> {"reliability": [...], "commissioning": {...} | None, "warehouse": [...]}"""

    def clip_path(self, name):
        """Absolute filesystem path for an evidence clip, or None."""
        return None


class ClockSource(Source):
    """-> {"sim_time": datetime | iso, "speed": float}"""

    def set(self, sim_time=None, speed=None):
        raise NotImplementedError


class MessageSource(Source):
    """-> {"messages": [{"id", "channel", "author", "bot", "text", "at"}]}"""


class SystemSource(Source):
    """-> {"vllm": {...}, "gpu": {...}, "services": [...], "cloud_model_calls": int | None}"""
