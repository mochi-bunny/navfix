"""Time helpers. Everything is wall-clock America/New_York (spec §16).

The demo day is in EDT, so a fixed -04:00 offset is used when zoneinfo/tzdata
is unavailable. All times leave the server as ISO strings with an offset.
"""
from datetime import datetime, timedelta, timezone

_TZ = None


def configure(tz_name="America/New_York", fallback_offset="-04:00"):
    global _TZ
    try:
        from zoneinfo import ZoneInfo
        _TZ = ZoneInfo(tz_name)
    except Exception:
        sign = -1 if fallback_offset.startswith("-") else 1
        hh, mm = fallback_offset.lstrip("+-").split(":")
        _TZ = timezone(sign * timedelta(hours=int(hh), minutes=int(mm)))


def tz():
    if _TZ is None:
        configure()
    return _TZ


def at(date_str, hhmm):
    """'2026-10-03', '13:25' -> aware datetime."""
    return parse(f"{date_str}T{hhmm}")


def parse(value, date_str=None):
    """Parse ISO, 'YYYY-MM-DDTHH:MM', 'HH:MM' (needs date_str) or epoch seconds/ms."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        secs = value / 1000.0 if value > 1e11 else float(value)
        return datetime.fromtimestamp(secs, tz())
    else:
        s = str(value).strip().replace(" ", "T", 1)
        if len(s) <= 5 and ":" in s:
            if not date_str:
                raise ValueError(f"time-only value {value!r} needs a date")
            s = f"{date_str}T{s}"
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz())
    return dt.astimezone(tz())


def iso(dt):
    return dt.isoformat(timespec="seconds") if dt else None


def epoch_ms(dt):
    return int(dt.timestamp() * 1000)


def hhmm(dt):
    """13:07 -> '1:07 PM' (used only for mock message text)."""
    h = dt.hour % 12 or 12
    return f"{h}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"
