"""receiver.py – where the translations come from.

LinuxVR-ViewShot passes every new translation on in two ways (ViewShot:
Options → General → Output), and this file listens to either:

    OscReceiver   UDP on 127.0.0.1:<port>, message /viewshot/translation
                  with three strings: translation, original, source
    FileWatcher   translation.json, written in one step by ViewShot:
                  {"id", "time", "translation", "original", "source"}
                  a new "id" = a new translation, even with the same text

Both run in their own thread, call ``on_new(translation, original,
source)`` for every new translation and ``on_idle()`` about four times a
second, so main.py can switch the chatbox back on time. Stdlib only.
"""

import json
import os
import socket
import struct
import threading
import time
from pathlib import Path

OSC_ADDRESS = "/viewshot/translation"
DEFAULT_FILE = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") \
    / "linuxvr-viewshot" / "translation.json"
IDLE_S = 0.25


# ------------------------------------------------------------------ OSC
def _read_string(data, i):
    """OSC string at offset i → (text, offset after the 4-byte padding)."""
    end = data.index(b"\0", i)
    return data[i:end].decode("utf-8", "replace"), (end + 4) & ~3


def parse_osc(data):
    """One OSC message → (address, [args]). Bundles and garbage → None.
    Knows s / i / f / T / F – everything ViewShot sends and a bit more."""
    try:
        if data.startswith(b"#bundle"):
            return None
        address, i = _read_string(data, 0)
        if not address.startswith("/"):
            return None
        if i >= len(data):
            return address, []
        tags, i = _read_string(data, i)
        args = []
        for tag in tags[1:] if tags.startswith(",") else "":
            if tag == "s":
                value, i = _read_string(data, i)
            elif tag == "i":
                value, i = struct.unpack_from(">i", data, i)[0], i + 4
            elif tag == "f":
                value, i = struct.unpack_from(">f", data, i)[0], i + 4
            elif tag in "TF":
                value = tag == "T"
            else:
                break       # a type we do not know – stop, keep what we have
            args.append(value)
        return address, args
    except (ValueError, struct.error):
        return None


class _Worker:
    """A thread that can be stopped: run() calls poll() until stop()."""

    def __init__(self, on_new, on_idle, on_status):
        self.on_new = on_new
        self.on_idle = on_idle
        self.on_status = on_status
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, name=type(self).__name__, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)   # makes a restart on the same port work
            self._thread = None

    def _run(self):
        while not self._stop.is_set():
            try:
                self.poll()
            except Exception as e:  # noqa: BLE001 – never let the thread die silently
                self.on_status(f"⚠ {e}")
                self._stop.wait(1.0)
            self.on_idle()
        self.close()

    def poll(self):
        raise NotImplementedError

    def close(self):
        pass


class OscReceiver(_Worker):
    """Listens on 127.0.0.1 only – nobody else on the network can write
    into your chatbox."""

    def __init__(self, port, on_new, on_idle, on_status):
        super().__init__(on_new, on_idle, on_status)
        self.port = int(port)
        self.sock = None
        self._next_try = 0.0

    def _open(self):
        if time.monotonic() < self._next_try:
            self._stop.wait(IDLE_S)
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(("127.0.0.1", self.port))
        except OSError as e:
            sock.close()
            self.on_status(f"⚠ Port {self.port} is taken ({e.strerror or e}) – trying again …")
            self._next_try = time.monotonic() + 3.0
            return
        sock.settimeout(IDLE_S)
        self.sock = sock
        self.on_status(f"✔ Listening on 127.0.0.1:{self.port}")

    def poll(self):
        if self.sock is None:
            self._open()
            return
        try:
            data = self.sock.recv(65535)
        except socket.timeout:
            return
        msg = parse_osc(data)
        if not msg or msg[0] != OSC_ADDRESS:
            return
        args = [a if isinstance(a, str) else str(a) for a in msg[1]] + ["", "", ""]
        if args[0].strip():
            self.on_new(args[0], args[1], args[2] or "photo")

    def close(self):
        if self.sock is not None:
            self.sock.close()
            self.sock = None


class FileWatcher(_Worker):
    """Checks the file's modification time a few times a second and reads
    it only when it changed. The "id" from before the start is skipped, so
    switching the plugin on never shows an old translation."""

    def __init__(self, path, on_new, on_idle, on_status):
        super().__init__(on_new, on_idle, on_status)
        text = str(path or "").strip()
        self.path = Path(os.path.expanduser(text)) if text else DEFAULT_FILE
        self._mtime = None
        self._last_id = None
        self._first = True

    def _read(self):
        try:
            entry = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return entry if isinstance(entry, dict) else None

    def poll(self):
        self._stop.wait(IDLE_S)
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            if self._first or self._mtime is not None:
                self.on_status(f"… Waiting for {self.path}")
            self._first, self._mtime = False, None
            return
        if mtime == self._mtime:
            return
        self._mtime = mtime
        entry = self._read()
        if entry is None:
            return
        new_id = entry.get("id")
        if self._first:          # what was there before we started
            self._first = False
            self._last_id = new_id
            self.on_status(f"✔ Watching {self.path}")
            return
        if self._last_id is None:
            self.on_status(f"✔ Watching {self.path}")
        if new_id == self._last_id:
            return
        self._last_id = new_id
        translation = str(entry.get("translation") or "")
        if translation.strip():
            self.on_new(translation, str(entry.get("original") or ""), str(entry.get("source") or "photo"))
