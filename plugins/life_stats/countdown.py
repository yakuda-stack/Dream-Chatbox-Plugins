"""{timer} - a countdown.

Two ways to use it, picked in the settings:

  until     counts down to a clock time ("22:00", every day) or to a
            date and time ("2026-12-31 23:59", once). Nothing to start:
            it is always running.
  duration  a kitchen timer: set the minutes, press Start. Survives a
            restart of the app, because the end time is stored, not a
            counter.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import datetime
import time


def parse_target(text, today=None):
    """"22:00" -> today's (or tomorrow's) 22:00; "2026-12-31 23:59" ->
    that moment. None when it cannot be read."""
    text = (text or "").strip()
    if not text:
        return None
    today = today or datetime.datetime.now()
    for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M",
                    "%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.datetime.strptime(text, pattern)
        except ValueError:
            pass
    for pattern in ("%H:%M", "%H:%M:%S"):
        try:
            t = datetime.datetime.strptime(text, pattern).time()
        except ValueError:
            continue
        moment = datetime.datetime.combine(today.date(), t)
        if moment <= today:
            moment += datetime.timedelta(days=1)
        return moment
    return None


def remaining(get, now=None):
    """Seconds left, or None when the timer has nothing to count."""
    now = time.time() if now is None else now
    if str(get("timer_mode", "until")) == "duration":
        try:
            end = float(get("timer_end", 0) or 0)
        except (TypeError, ValueError):
            end = 0.0
        if not end:
            return None
        return end - now
    target = parse_target(get("timer_target", ""),
                          datetime.datetime.fromtimestamp(now))
    if target is None:
        return None
    return target.timestamp() - now


def format_left(seconds, style="auto"):
    seconds = int(max(0, round(seconds)))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if style == "clock":
        total_h = days * 24 + hours
        return (f"{total_h}:{minutes:02d}:{secs:02d}" if total_h
                else f"{minutes}:{secs:02d}")
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def values(get, vals, now=None):
    if not get("timer", False):
        return
    left = remaining(get, now)
    if left is None:
        return
    icon = str(get("timer_icon", "") or "").strip()
    if left <= 0:
        done = str(get("timer_done", "") or "").strip()
        vals["timer"] = f"{icon} {done}".strip() if done else None
        return
    label = str(get("timer_label", "") or "").strip()
    text = format_left(left, str(get("timer_style", "auto")))
    vals["timer"] = " ".join(p for p in (icon, label, text) if p)
