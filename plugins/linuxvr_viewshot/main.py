"""LinuxVR-ViewShot – show ViewShot's translations in the chatbox.

ViewShot (VR photos with a hand-frame gesture, Linux) reads the text in a
photo and translates it. With its Output switched on (ViewShot: Options →
General → Output) every new translation reaches this plugin – by OSC or
through translation.json – and is shown in the chatbox for a few seconds.
Afterwards the chatbox goes back to whatever you normally send.

    receiver.py   OSC listener / file watcher (own thread)
    main.py       what is shown, for how long, and where

How the chatbox is taken over: on_text() is the last look at the finished
text before it is sent. While a translation is "on", it returns the
translation instead (or puts it on top). api.refresh() makes the app send
right away when one arrives and again when its time is up.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import threading
import time

from .receiver import FileWatcher, OscReceiver

CHATBOX_LIMIT = 144

_api = None
_lock = threading.Lock()
_worker = None
_worker_key = None
_current = None   # {"translation", "original", "source", "until"} while shown
_last = None      # the latest one, shown or not


# --------------------------------------------------------------- setup
def setup(api):
    global _api
    _api = api
    _restart()


def teardown():
    global _worker, _worker_key, _current
    if _worker is not None:
        _worker.stop()
    _worker = _worker_key = None
    with _lock:
        _current = None


def on_settings(options):
    # only a new source / port / file needs a new thread – the rest is read when used
    if _key() != _worker_key:
        _restart()


def _key():
    source = _api.get("source", "osc")
    return (source, int(_api.get("port", 9025) or 9025) if source == "osc" else str(_api.get("file", "") or ""))


def _restart():
    global _worker, _worker_key
    if _worker is not None:
        _worker.stop()
    _worker_key = _key()
    if _worker_key[0] == "osc":
        _worker = OscReceiver(_worker_key[1], _on_new, _on_idle, _status)
    else:
        _worker = FileWatcher(_worker_key[1], _on_new, _on_idle, _status)
    _worker.start()


def _status(text):
    if _api is not None and _api.supports("api.set"):
        _api.set("status", text)


# ------------------------------------------------- a translation arrives
def _on_new(translation, original, source):
    """From the worker thread – no widgets here, api.refresh() is thread-safe."""
    global _current, _last
    if source == "photo" and not _api.get("show_photo", True):
        return
    if source == "lens" and not _api.get("show_lens", True):
        return
    seconds = max(2, min(10, int(_api.get("seconds", 5) or 5)))
    entry = {"translation": translation.strip(), "original": original.strip(),
             "source": source, "until": time.monotonic() + seconds}
    with _lock:
        _current = entry
        _last = entry
    _api.log(f"{source}: {entry['translation'][:60]!r} for {seconds} s")
    _api.refresh()


def _on_idle():
    """A few times a second from the worker: time up → normal chatbox again."""
    global _current
    with _lock:
        expired = _current is not None and time.monotonic() >= _current["until"]
        if expired:
            _current = None
    if expired:
        _api.refresh()


# ------------------------------------------------------------- the text
def _clip(text, limit=CHATBOX_LIMIT):
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _lines(text):
    return [line.strip() for line in text.splitlines() if line.strip()]


def _message(entry):
    """What goes into the chatbox: prefix + translation (+ original)."""
    one_line = bool(_api.get("one_line", False))
    join = " · " if one_line else "\n"
    translation = join.join(_lines(entry["translation"]))
    if _api.get("with_original", False) and entry["original"]:
        original = join.join(_lines(entry["original"]))
        translation = f"{original}{join}➜ {translation}"
    prefix = str(_api.get("prefix", "🌐") or "").strip()
    return _clip(f"{prefix} {translation}".strip())


def on_text(text):
    with _lock:
        entry = _current
    mode = _api.get("mode", "replace") if _api else "placeholder"
    if entry is None or mode == "placeholder":
        return text
    message = _message(entry)
    if mode == "top" and text:
        return _clip(f"{message}\n{text}")
    return message


def get_values():
    """{linuxvr_viewshot_translation} etc. – only while a translation is shown
    (None = the placeholder and its separator disappear), {…_last} always."""
    with _lock:
        entry, last = _current, _last
    return {
        "translation": _message(entry) if entry else None,
        "original": (entry["original"] or None) if entry else None,
        "source": entry["source"] if entry else None,
        "last": " ".join(_lines(last["translation"])) if last else None,
    }


# --------------------------------------------------------------- button
def on_action(key):
    if key == "test":
        _on_new("ViewShot test ✔ – this is how a translation looks", "Original text", "test")
        return f"shown for {int(_api.get('seconds', 5) or 5)} s"
    return ""
