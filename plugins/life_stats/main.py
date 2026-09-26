"""Life Stats - the "about you" values for the chatbox line.

  clock      {realtime} {realdate} {realday} {realtime_alt} {timezone}
             moved here from World Stats (v1.7.0 of that plugin), with
             its settings carried over on the first start
  countdown  {timer}
  weather    {weather} {weather_temp} {weather_feels_like}
             {weather_condition} {weather_emoji} {weather_humidity}
             {weather_wind}                       - Open-Meteo, no key
  heart rate {heartrate} {heartrate_avg} {heartrate_min} {heartrate_max}
             {heartrate_trend}                    - Pulsoid or HypeRate
  files      {file_text} {file_text_2} {file_text_3}

World Stats keeps what comes from VRChat and the headset. Every part
here works on its own; the names are the ones the chatbox converter
(chatbox-converter.github.io) uses, so a converted setup just works.
All of them are global_placeholders: status texts, custom strings, AIO.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import time

from . import clock, countdown, filetext, heartrate, weather

_api = None
_weather = None
_heart = None
_written = {}
_last_write = {}
MIN_WRITE_SEC = 2.0

#: settings World Stats had for its clock - copied over once
CLOCK_KEYS = ("clock", "clock_format", "clock_tz", "clock_date",
              "date_format", "clock_alt", "alt_tz", "alt_format",
              "alt_label")
MIGRATED_MARK = "migrated_from_world_stats"

KEYS = ("realtime", "realdate", "realday", "realtime_alt", "timezone",
        "timer",
        "weather", "weather_temp", "weather_feels_like", "weather_condition",
        "weather_emoji", "weather_humidity", "weather_wind",
        "heartrate", "heartrate_avg", "heartrate_min", "heartrate_max",
        "heartrate_trend",
        "file_text", "file_text_2", "file_text_3")


# ---------------------------------------------------------------- setup
def setup(api):
    global _api
    _api = api
    _migrate_clock()
    _apply()


def teardown():
    global _weather, _heart
    for worker in (_weather, _heart):
        if worker is not None:
            try:
                worker.stop()
            except Exception:
                pass
    _weather = _heart = None
    _written.clear()
    _last_write.clear()
    _invalidate()


def on_event(name, data=None):
    if name == "app.shutdown":
        teardown()


def on_settings(settings):
    _invalidate()
    _apply()


def _apply():
    """Starts a worker the moment its part is switched on - and not
    before: with weather and heart rate off nothing touches the network."""
    global _weather, _heart
    if _get("weather", False) and _weather is None:
        _weather = weather.WeatherWorker(_weather_conf, _log)
        _weather.start()
    if _get("heartrate", False) and _heart is None:
        _heart = heartrate.HeartRateWorker(_heart_conf, _log)
        _heart.start()
    _sync_status()


def _weather_conf():
    try:
        interval = int(_get("weather_every", 15) or 15) * 60
    except (TypeError, ValueError):
        interval = 15 * 60
    return (bool(_get("weather", False)), str(_get("weather_place", "")),
            interval, _get("weather_unit", "celsius") == "fahrenheit",
            _get("weather_wind_unit", "kmh") == "mph")


def _heart_conf():
    return (bool(_get("heartrate", False)),
            str(_get("heartrate_source", "pulsoid")),
            str(_get("pulsoid_token", "")),
            str(_get("hyperate_id", "")),
            str(_get("hyperate_key", "")))


# ------------------------------------------------- World Stats -> here
def _migrate_clock():
    """Copies the clock settings over from World Stats, once. Without
    this, everyone who had a clock there would find it reset to the
    defaults after installing this plugin."""
    if _api is None:
        return
    try:
        mark = _api.data_path(MIGRATED_MARK)
    except Exception:
        return
    if mark.exists():
        return
    try:
        old = (_api.plugin_dir.parent / "world_stats" / "configs"
               / "config.json")
        opts = json.loads(old.read_text(encoding="utf-8")).get("options") \
            or {}
    except Exception:
        opts = {}
    carried = {k: opts[k] for k in CLOCK_KEYS if k in opts}
    if carried and hasattr(_api, "set_many"):
        _api.set_many(carried)
        _log(f"clock settings taken over from World Stats: "
             f"{', '.join(sorted(carried))}")
    try:
        mark.write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    except Exception:
        pass


# ------------------------------------------------------------- helpers
def _log(msg):
    fn = getattr(_api, "log", None) if _api is not None else None
    if callable(fn):
        try:
            fn(msg)
            return
        except Exception:
            pass
    print(f"[life_stats] {msg}")


def _get(key, default=None):
    if _api is None:
        return default
    try:
        return _api.get(key, default)
    except Exception:
        return default


def _set(key, value):
    """A settings row, only when it changed and at most every
    MIN_WRITE_SEC - api.set() goes to disk."""
    if _api is None or _written.get(key) == value:
        return
    now = time.monotonic()
    if now - _last_write.get(key, -1e9) < MIN_WRITE_SEC:
        return
    _written[key] = value
    _last_write[key] = now
    setter = getattr(_api, "set", None)
    if callable(setter):
        try:
            setter(key, value)
        except Exception:
            pass


def _sync_status():
    """The read-only Status rows. Runs on the GUI thread only."""
    _set("weather_status", _weather.status if _weather else "off")
    _set("heartrate_status", _heart.status if _heart else "off")
    left = countdown.remaining(_get)
    if _get("timer_mode", "until") == "duration" and left is None:
        _set("timer_status", "not running - press Start")
    elif left is None:
        _set("timer_status", "no target set")
    elif left <= 0:
        _set("timer_status", "done")
    else:
        _set("timer_status", "running - " + countdown.format_left(
            left, "clock"))


# -------------------------------------------------------------- actions
def on_action(key):
    if key == "weather_now":
        if _weather is None:
            return "switch the weather on first"
        _weather.poll_now()
        return "asking Open-Meteo …"
    if key == "timer_start":
        try:
            minutes = float(_get("timer_minutes", 10) or 10)
        except (TypeError, ValueError):
            minutes = 10.0
        if hasattr(_api, "set"):
            _api.set("timer_mode", "duration")
            _api.set("timer_end", time.time() + minutes * 60)
        _invalidate()
        return f"started - {countdown.format_left(minutes * 60)}"
    if key == "timer_stop":
        if hasattr(_api, "set"):
            _api.set("timer_end", 0)
        _invalidate()
        return "stopped"
    if key == "heartrate_reset":
        if _heart is not None:
            _heart.stats.reset()
        return "average, min and max start fresh"
    return None


# --------------------------------------------------------------- values
_VALUES_TTL = 0.1
_cache = (0.0, None)


def _invalidate():
    global _cache
    _cache = (0.0, None)


def _compute():
    vals = {k: None for k in KEYS}
    clock.values(_get, vals, _log)
    countdown.values(_get, vals)
    weather.values(_get, vals, _weather.snapshot() if _weather else None)
    heartrate.values(_get, vals,
                     _heart.stats.snapshot() if _heart else None)
    filetext.values(_get, vals)
    _sync_status()
    return vals


def get_values():
    """Every placeholder this plugin owns; off or unknown stays None,
    which drops it together with its separators. One pass is reused for
    _VALUES_TTL, because the host asks three times per frame."""
    global _cache
    now = time.monotonic()
    when, cached = _cache
    if cached is not None and now - when < _VALUES_TTL:
        return dict(cached)
    vals = _compute()
    _cache = (now, vals)
    return dict(vals)


def get_text():
    """The combined line -> {life_stats}."""
    vals = get_values()
    parts = [vals["realtime"], vals["realtime_alt"], vals["timer"],
             vals["weather"], vals["heartrate"], vals["file_text"]]
    return " | ".join(p for p in parts if p)


def get_lines():
    text = get_text()
    return [text] if text else []
