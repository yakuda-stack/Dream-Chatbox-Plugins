"""Weather from Open-Meteo - free, no account, no API key.

    https://geocoding-api.open-meteo.com/v1/search   city -> lat/lon
    https://api.open-meteo.com/v1/forecast            current weather

The place is looked up once (and again only when the setting changes);
the weather itself is polled every few minutes on a background thread.
get_values() only ever reads the last snapshot, so a slow answer can
delay an update but never the chatbox.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import threading
import time

from . import netutil

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
API_URL = "https://api.open-meteo.com/v1/forecast"
MIN_INTERVAL = 5 * 60          # Open-Meteo updates every 15 min anyway

# WMO weather codes -> (emoji, English, German)
WMO = {
    0: ("☀️", "Clear", "Klar"),
    1: ("\U0001F324️", "Mostly clear", "Überwiegend klar"),
    2: ("⛅", "Partly cloudy", "Teilweise bewölkt"),
    3: ("☁️", "Overcast", "Bedeckt"),
    45: ("\U0001F32B️", "Fog", "Nebel"),
    48: ("\U0001F32B️", "Rime fog", "Reifnebel"),
    51: ("\U0001F326️", "Light drizzle", "Leichter Niesel"),
    53: ("\U0001F326️", "Drizzle", "Niesel"),
    55: ("\U0001F326️", "Heavy drizzle", "Starker Niesel"),
    56: ("\U0001F327️", "Freezing drizzle", "Gefrierender Niesel"),
    57: ("\U0001F327️", "Freezing drizzle", "Gefrierender Niesel"),
    61: ("\U0001F326️", "Light rain", "Leichter Regen"),
    63: ("\U0001F327️", "Rain", "Regen"),
    65: ("\U0001F327️", "Heavy rain", "Starker Regen"),
    66: ("\U0001F327️", "Freezing rain", "Eisregen"),
    67: ("\U0001F327️", "Freezing rain", "Eisregen"),
    71: ("\U0001F328️", "Light snow", "Leichter Schnee"),
    73: ("\U0001F328️", "Snow", "Schnee"),
    75: ("❄️", "Heavy snow", "Starker Schnee"),
    77: ("\U0001F328️", "Snow grains", "Schneegriesel"),
    80: ("\U0001F326️", "Showers", "Schauer"),
    81: ("\U0001F327️", "Showers", "Schauer"),
    82: ("⛈️", "Heavy showers", "Starke Schauer"),
    85: ("\U0001F328️", "Snow showers", "Schneeschauer"),
    86: ("\U0001F328️", "Snow showers", "Schneeschauer"),
    95: ("⛈️", "Thunderstorm", "Gewitter"),
    96: ("⛈️", "Thunderstorm, hail", "Gewitter mit Hagel"),
    99: ("⛈️", "Thunderstorm, hail", "Gewitter mit Hagel"),
}


def describe(code, lang="en"):
    emoji, en, de = WMO.get(int(code), ("\U0001F321️", "", ""))
    return emoji, (de if lang == "de" else en)


class WeatherWorker(threading.Thread):
    """Polls Open-Meteo for one place. read_conf() returns
    (enabled, place, interval_s, fahrenheit, mph)."""

    def __init__(self, read_conf, log):
        super().__init__(name="life_stats-weather", daemon=True)
        self._read = read_conf
        self.log = log
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._state = {}
        self._place_key = None
        self._place = None             # (lat, lon, label)
        self._last_poll = 0.0
        self._last_units = None
        self.status = "off"

    def snapshot(self):
        with self._lock:
            return dict(self._state)

    def poll_now(self):
        self._last_poll = 0.0
        self._wake.set()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def run(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:          # never let the thread die
                self.status = f"error: {e}"
                self.log(f"weather: {e}")
            self._wake.wait(5.0)
            self._wake.clear()

    def _tick(self):
        enabled, place, interval, fahrenheit, mph = self._read()
        if not enabled:
            self.status = "off"
            return
        place = (place or "").strip()
        if not place:
            self.status = "no place set"
            with self._lock:
                self._state = {}
            return
        if place != self._place_key:
            self._place = self._geocode(place)
            self._place_key = place
            self._last_poll = 0.0
        if self._place is None:
            return
        units = (fahrenheit, mph)
        if units != self._last_units:
            self._last_units = units
            self._last_poll = 0.0
        if time.time() - self._last_poll < max(MIN_INTERVAL, interval):
            return
        self._last_poll = time.time()
        lat, lon, label = self._place
        data = netutil.request_json(netutil.build_url(
            API_URL, latitude=f"{lat:.4f}", longitude=f"{lon:.4f}",
            current=("temperature_2m,apparent_temperature,"
                     "relative_humidity_2m,weather_code,wind_speed_10m,"
                     "is_day"),
            temperature_unit="fahrenheit" if fahrenheit else "celsius",
            wind_speed_unit="mph" if mph else "kmh",
            timezone="auto"))
        cur = data.get("current") or {}
        if "temperature_2m" not in cur:
            raise netutil.HttpError(200, "no current weather in the answer")
        with self._lock:
            self._state = {
                "temp": cur.get("temperature_2m"),
                "feels": cur.get("apparent_temperature"),
                "humidity": cur.get("relative_humidity_2m"),
                "code": cur.get("weather_code"),
                "wind": cur.get("wind_speed_10m"),
                "is_day": cur.get("is_day", 1),
                "unit": "°F" if fahrenheit else "°C",
                "wind_unit": "mph" if mph else "km/h",
                "place": label,
                "at": time.time(),
            }
        self.status = f"{label}: {cur.get('temperature_2m')}" \
                      f"{self._state['unit']}"

    def _geocode(self, place):
        """"Berlin" or "Berlin, DE" -> (lat, lon, label). "52.52,13.41"
        is taken as coordinates directly."""
        parts = [p.strip() for p in place.split(",")]
        if len(parts) == 2:
            try:
                return float(parts[0]), float(parts[1]), place
            except ValueError:
                pass
        name, country = parts[0], (parts[1].upper() if len(parts) > 1
                                   else "")
        data = netutil.request_json(netutil.build_url(
            GEO_URL, name=name, count=10, language="en", format="json"))
        hits = data.get("results") or []
        if country:
            hits = [h for h in hits
                    if str(h.get("country_code", "")).upper() == country
                    or str(h.get("country", "")).upper() == country] or hits
        if not hits:
            self.status = f"place {place!r} not found"
            self.log(f"weather: {self.status}")
            return None
        hit = hits[0]
        label = ", ".join(x for x in (hit.get("name"),
                                      hit.get("country_code")) if x)
        self.log(f"weather: {place!r} -> {label} "
                 f"({hit['latitude']:.2f}, {hit['longitude']:.2f})")
        return float(hit["latitude"]), float(hit["longitude"]), label


def _num(value, digits=0):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return f"{value:.{digits}f}" if digits else str(int(round(value)))


def values(get, vals, snap):
    """Fills the weather_* placeholders from a worker snapshot."""
    if not get("weather", False) or not snap:
        return
    lang = str(get("weather_lang", "en"))
    unit = snap.get("unit", "°C")
    code = snap.get("code")
    emoji, text = describe(-1 if code is None else code, lang)
    if snap.get("code") in (0, 1) and not snap.get("is_day", 1):
        emoji = "\U0001F319"            # clear night: a moon, not a sun
    temp = _num(snap.get("temp"))
    feels = _num(snap.get("feels"))
    hum = _num(snap.get("humidity"))
    wind = _num(snap.get("wind"))
    vals["weather_temp"] = f"{temp}{unit}" if temp is not None else None
    vals["weather_feels_like"] = f"{feels}{unit}" if feels is not None \
        else None
    vals["weather_condition"] = text or None
    vals["weather_emoji"] = emoji or None
    vals["weather_humidity"] = f"{hum}%" if hum is not None else None
    vals["weather_wind"] = (f"{wind} {snap.get('wind_unit', 'km/h')}"
                            if wind is not None else None)
    vals["weather"] = " ".join(p for p in (
        vals["weather_emoji"] if get("weather_show_emoji", True) else "",
        vals["weather_temp"]) if p) or None
