"""Notifications - push events out of the chatbox instead of into it.

Port of `Notifications` from Bluscream's VRCOSC-Modules. Upstream the
targets are Windows toasts, XSOverlay, OVRToolkit and webhooks, and the
trigger comes from flow nodes. Here the trigger is the suite itself: when
a value you picked changes - a new IRC message, a friend event from VRCX,
a different track, a changed HTTP answer - the new text goes out.

Targets: `notify-send` on the desktop, XSOverlay over UDP 42010, and any
webhook that accepts JSON. OVRToolkit is left out: its WebSocket needs a
library the chatbox does not ship.

Sending happens on its own worker thread. A slow webhook must never make
the chatbox stutter, so the queue drops rather than waits.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import queue
import socket
import subprocess
import threading

from . import netutil, util

ID = "nt"
NAME = "Notifications"

KEYS = ("nt_last", "nt_count")

XSOVERLAY = ("127.0.0.1", 42010)
QUEUE_MAX = 20

# setting -> the placeholder whose change fires a notification
WATCH = {
    "nt_on_irc": "irc_msg",
    "nt_on_http": "ht_text",
    "nt_on_ha": "ha_all",
    "nt_on_vrcx": "vx_last_friend",
    "nt_on_media": "md_media",
}

_worker = None
_queue = None
_stop = None
_seen = {}
_last = ""
_count = 0
_lock = threading.Lock()


def start(ctx):
    global _worker, _queue, _stop, _seen, _last, _count
    stop()
    _seen, _last, _count = {}, "", 0
    _queue = queue.Queue(maxsize=QUEUE_MAX)
    _stop = threading.Event()
    _worker = threading.Thread(target=_loop, args=(ctx,), name="notify",
                               daemon=True)
    _worker.start()


def stop():
    global _worker, _queue, _stop
    if _stop is not None:
        _stop.set()
    if _queue is not None:
        try:
            _queue.put_nowait(None)
        except queue.Full:
            pass
    if _worker is not None:
        _worker.join(timeout=3.0)
    _worker = None
    _queue = None
    _stop = None


def on_settings(ctx):
    """Nothing to restart - every setting is read at send time."""


# ------------------------------------------------------------- trigger
def observe(ctx, vals):
    """Called by main.py with the merged values of every module."""
    if _queue is None:
        return
    for key, placeholder in WATCH.items():
        if not ctx.flag(key):
            continue
        current = vals.get(placeholder)
        if not current or _seen.get(placeholder) == current:
            _seen[placeholder] = current
            continue
        first_run = placeholder not in _seen
        _seen[placeholder] = current
        # the first value after a start is not an event, it is a state
        if not first_run:
            _push(ctx, current)


def _push(ctx, text):
    global _last, _count
    with _lock:
        _last = str(text)
        _count += 1
    try:
        _queue.put_nowait((ctx.text("nt_title", "OSC-DreamChatbox"), str(text)))
    except (queue.Full, AttributeError):
        pass                               # backed up: drop, never block


# -------------------------------------------------------------- worker
def _loop(ctx):
    while not _stop.is_set():
        try:
            item = _queue.get(timeout=0.5)
        except queue.Empty:
            continue
        if item is None:
            return
        title, text = item
        if ctx.flag("nt_desktop"):
            _desktop(title, text)
        if ctx.flag("nt_xsoverlay"):
            _xsoverlay(ctx, title, text)
        webhook = ctx.text("nt_webhook")
        if webhook:
            _webhook(webhook, title, text)


def _desktop(title, text):
    if util.which("notify-send") is None:
        return
    try:
        subprocess.run(["notify-send", "-a", "OSC-DreamChatbox", title, text],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=5, check=False)
    except Exception:
        pass


def _xsoverlay(ctx, title, text):
    payload = {
        "messageType": 1,
        "index": 0,
        "timeout": ctx.num("nt_timeout", 3, 1, 30),
        "height": 100,
        "opacity": 1.0,
        "volume": 0.0,
        "audioPath": "",
        "title": title,
        "content": text,
        "useBase64Icon": False,
        "icon": "default",
        "sourceApp": "OSC-DreamChatbox",
    }
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(json.dumps(payload).encode("utf-8"), XSOVERLAY)
    except OSError:
        pass


def _webhook(url, title, text):
    try:
        netutil.post_json(url, {"title": title, "content": text,
                                "text": text}, timeout=8)
    except Exception:
        pass


# --------------------------------------------------------------- values
def values(ctx):
    vals = dict.fromkeys(KEYS)
    if _worker is None:
        return vals
    with _lock:
        last, count = _last, _count
    if last:
        vals["nt_last"] = util.cut(last, ctx.num("nt_max", 60, 4, 140)) or None
    vals["nt_count"] = str(count) if count else None
    return vals


def line(vals):
    return []          # notifications go outwards, not into the line
