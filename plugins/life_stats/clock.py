"""Clock, date, a second time zone and the zone name.

Moved here from World Stats in v1.7.0 of that plugin: the clock never
had anything to do with VRChat's log, and it sits better next to the
other "about you" values (weather, heart rate, countdown).
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import datetime

_tz_warned = set()


def now(tz_name, log=print):
    """Local time, or the time in a named zone.

    zoneinfo is stdlib, but on Windows it needs the tzdata package, so a
    bad or unavailable zone falls back to local time instead of leaving
    the placeholder empty."""
    tz_name = (tz_name or "").strip()
    if tz_name:
        try:
            from zoneinfo import ZoneInfo
            return datetime.datetime.now(ZoneInfo(tz_name))
        except Exception as e:
            if tz_name not in _tz_warned:
                _tz_warned.add(tz_name)
                log(f"time zone {tz_name!r} unusable ({e}) - "
                    f"using local time")
    return datetime.datetime.now().astimezone()


def fmt(moment, pattern, fallback):
    try:
        return moment.strftime(pattern) or None
    except Exception:
        return moment.strftime(fallback)


def zone_name(moment, style="short"):
    """{timezone}: "CEST", "UTC+02:00" or the IANA name when there is
    one. Windows often only knows a long name ("W. Europe Daylight
    Time") - then the UTC offset is the more useful answer."""
    if style == "offset":
        return _offset(moment)
    if style == "iana":
        key = getattr(moment.tzinfo, "key", "")
        if key:
            return key
    short = (moment.tzname() or "").strip()
    if short and len(short) <= 6 and " " not in short:
        return short
    return _offset(moment)


def _offset(moment):
    off = moment.utcoffset()
    if off is None:
        return "UTC"
    minutes = int(off.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    hours, mins = divmod(abs(minutes), 60)
    return f"UTC{sign}{hours}" + (f":{mins:02d}" if mins else "")


def values(get, vals, log=print):
    """Fills the clock placeholders. `get` reads a plugin setting."""
    if not get("clock", True):
        return
    moment = now(get("clock_tz", ""), log)
    pattern = str(get("clock_format", "%H:%M")).strip() or "%H:%M"
    vals["realtime"] = fmt(moment, pattern, "%H:%M")

    if get("clock_date", False):
        dpattern = str(get("date_format", "%d.%m.")).strip() or "%d.%m."
        vals["realdate"] = fmt(moment, dpattern, "%d.%m.")
        vals["realday"] = fmt(moment, "%a", "%a")

    if get("clock_zone", False):
        vals["timezone"] = zone_name(moment, str(get("zone_style", "short")))

    if get("clock_alt", False):
        apattern = str(get("alt_format", "%H:%M")).strip() or "%H:%M"
        alt = fmt(now(get("alt_tz", ""), log), apattern, "%H:%M")
        label = str(get("alt_label", "")).strip()
        vals["realtime_alt"] = f"{label} {alt}".strip() if alt else None
