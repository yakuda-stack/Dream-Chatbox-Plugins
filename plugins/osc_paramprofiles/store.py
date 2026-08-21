"""Profile storage.

One json file in the plugin's data dir, written atomically. The whole
file is small (a profile is a few dozen numbers) so it is read and
written whole rather than kept in a database - a user can open it, fix a
typo, and mail it to somebody.

Never touched from a worker thread; the panel owns it.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import os
import threading
import time
import uuid

FILE = "profiles.json"
UNSORTED = "Uncategorized"
SCHEMA = 1


def _blank():
    return {"schema": SCHEMA, "categories": [], "profiles": []}


class Store:
    """The profile list, plus the categories that outlive their last
    profile so an emptied category does not silently disappear."""

    def __init__(self, folder, log=None):
        self.folder = folder or "."
        self.path = os.path.join(self.folder, FILE)
        self._log = log or (lambda *a: None)
        self.data = _blank()

        self._queue_lock = threading.Lock()
        self._payload = None
        self._queued = False
        self._writing = False
        self._dirty = threading.Event()
        self._stop = threading.Event()
        self._writer = None

        self.load()

    # ------------------------------------------------------------- io
    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except FileNotFoundError:
            self.data = _blank()
            return
        except (OSError, ValueError) as exc:
            self._log(f"profiles.json unreadable ({exc}) - starting empty, "
                      "the old file is left alone")
            self.data = _blank()
            return
        if not isinstance(raw, dict):
            self.data = _blank()
            return
        self.data = {
            "schema": SCHEMA,
            "categories": [str(c) for c in raw.get("categories", [])
                           if str(c).strip()],
            "profiles": [self._clean(p) for p in raw.get("profiles", [])
                         if isinstance(p, dict)],
        }

    def save(self):
        """Hand the write to a background thread and return immediately.

        The write itself is unchanged - temp file, fsync, atomic replace -
        but fsync is a disk barrier, and on btrfs with VRChat and WiVRn
        both hammering the same device it can block for well over a
        second. Doing that on the GUI thread is why a new profile took
        so long to appear.

        The payload is serialised here, on the caller's thread, rather
        than in the writer: json.dumps of a profile list is sub-millisecond,
        and it means the writer never reads self.data while the GUI is
        mutating it.
        """
        try:
            payload = json.dumps(self.data, indent=2, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            self._log(f"could not serialise profiles: {exc}")
            return False
        with self._queue_lock:
            self._payload = payload
            self._queued = True
        self._dirty.set()
        self._ensure_writer()
        return True

    def pending(self):
        """True while a write is queued or in flight - the panel shows
        'saving…' on the row until this goes quiet."""
        with self._queue_lock:
            return self._queued or self._writing

    def flush(self, timeout=3.0):
        """Block until the queue is empty. Only for teardown."""
        deadline = time.monotonic() + timeout
        while self.pending() and time.monotonic() < deadline:
            time.sleep(0.02)
        return not self.pending()

    def close(self):
        self.flush()
        self._stop.set()
        self._dirty.set()

    def _ensure_writer(self):
        with self._queue_lock:
            if self._writer is not None and self._writer.is_alive():
                return
            self._writer = threading.Thread(target=self._write_loop,
                                            name="paramprofiles-store",
                                            daemon=True)
            self._writer.start()

    def _write_loop(self):
        while not self._stop.is_set():
            if not self._dirty.wait(0.5):
                continue
            self._dirty.clear()
            with self._queue_lock:
                if not self._queued:
                    continue
                # coalesce: five edits in a row become one write
                payload, self._payload = self._payload, None
                self._queued = False
                self._writing = True
            try:
                self._write(payload)
            finally:
                with self._queue_lock:
                    self._writing = False

    def _write(self, payload):
        try:
            os.makedirs(self.folder, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
            return True
        except OSError as exc:
            self._log(f"could not save profiles: {exc}")
            return False

    def save_blocking(self):
        """For callers that must know the bytes are on disk."""
        self.save()
        return self.flush()

    # -------------------------------------------------------- profiles
    @staticmethod
    def _clean(raw):
        params = {}
        for name, entry in (raw.get("params") or {}).items():
            if isinstance(entry, dict):
                params[str(name)] = {"t": str(entry.get("t", "f"))[:1],
                                     "v": entry.get("v")}
            else:  # tolerate a hand-written {"Name": 0.5}
                tag = "b" if isinstance(entry, bool) else (
                    "i" if isinstance(entry, int) else
                    "f" if isinstance(entry, float) else "s")
                params[str(name)] = {"t": tag, "v": entry}
        return {
            "id": str(raw.get("id") or uuid.uuid4().hex[:12]),
            "name": str(raw.get("name") or "unnamed"),
            "category": str(raw.get("category") or ""),
            "avatar_id": str(raw.get("avatar_id") or ""),
            "notes": str(raw.get("notes") or ""),
            "created": float(raw.get("created") or time.time()),
            "updated": float(raw.get("updated") or time.time()),
            "params": params,
        }

    def profiles(self):
        return self.data["profiles"]

    def by_id(self, pid):
        for profile in self.data["profiles"]:
            if profile["id"] == pid:
                return profile
        return None

    def add(self, name, category="", params=None, avatar_id="", notes=""):
        profile = self._clean({
            "id": uuid.uuid4().hex[:12], "name": name, "category": category,
            "avatar_id": avatar_id, "notes": notes, "params": params or {},
        })
        self.data["profiles"].append(profile)
        self.remember_category(category)
        self.save()
        return profile

    def update(self, pid, **fields):
        profile = self.by_id(pid)
        if profile is None:
            return None
        for key, value in fields.items():
            if key in ("name", "category", "notes", "avatar_id"):
                profile[key] = str(value)
            elif key == "params":
                profile["params"] = self._clean({"params": value})["params"]
        profile["updated"] = time.time()
        self.remember_category(profile["category"])
        self.save()
        return profile

    def duplicate(self, pid):
        source = self.by_id(pid)
        if source is None:
            return None
        copy = dict(source)
        copy["id"] = uuid.uuid4().hex[:12]
        copy["name"] = f"{source['name']} copy"
        copy["params"] = {k: dict(v) for k, v in source["params"].items()}
        copy["created"] = copy["updated"] = time.time()
        self.data["profiles"].append(copy)
        self.save()
        return copy

    def remove(self, pid):
        before = len(self.data["profiles"])
        self.data["profiles"] = [p for p in self.data["profiles"]
                                 if p["id"] != pid]
        if len(self.data["profiles"]) != before:
            self.save()
            return True
        return False

    # ------------------------------------------------------ categories
    def categories(self):
        names = {p["category"] for p in self.data["profiles"] if p["category"]}
        names.update(c for c in self.data["categories"] if c)
        return sorted(names, key=str.casefold)

    def remember_category(self, name):
        name = str(name or "").strip()
        if name and name not in self.data["categories"]:
            self.data["categories"].append(name)

    def rename_category(self, old, new):
        new = str(new or "").strip()
        for profile in self.data["profiles"]:
            if profile["category"] == old:
                profile["category"] = new
        self.data["categories"] = [new if c == old else c
                                   for c in self.data["categories"] if c]
        if new and new not in self.data["categories"]:
            self.data["categories"].append(new)
        self.save()

    def drop_category(self, name):
        """Deletes the category, not the profiles in it."""
        for profile in self.data["profiles"]:
            if profile["category"] == name:
                profile["category"] = ""
        self.data["categories"] = [c for c in self.data["categories"]
                                   if c != name]
        self.save()

    # --------------------------------------------------- import/export
    def export(self, path, ids=None):
        chosen = [p for p in self.data["profiles"]
                  if ids is None or p["id"] in ids]
        payload = {"schema": SCHEMA, "exported": time.time(),
                   "profiles": chosen}
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        return len(chosen)

    def import_file(self, path):
        """Imported profiles always get a fresh id, so importing the same
        file twice gives two profiles instead of silently overwriting
        one the user has since edited."""
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        incoming = raw.get("profiles") if isinstance(raw, dict) else raw
        if not isinstance(incoming, list):
            raise ValueError("no profile list in that file")
        added = 0
        for item in incoming:
            if not isinstance(item, dict):
                continue
            profile = self._clean(item)
            profile["id"] = uuid.uuid4().hex[:12]
            self.data["profiles"].append(profile)
            self.remember_category(profile["category"])
            added += 1
        if added:
            self.save()
        return added
