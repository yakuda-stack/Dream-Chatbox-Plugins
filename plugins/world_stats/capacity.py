"""{vrc_instance_capacity} - how many people fit into the current world.

The log does not say. VRChat's public world endpoint does, without a
login:

    GET https://api.vrchat.cloud/api/1/worlds/<wrld_…>  ->  "capacity"

Asked once per world (cached for the session), on a background thread,
and only while the setting is on. VRChat asks API clients to identify
themselves, hence the User-Agent.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import threading
import time
import urllib.error
import urllib.request

API = "https://api.vrchat.cloud/api/1/worlds/"
USER_AGENT = ("OSC-DreamChatbox-world_stats "
              "(+https://github.com/yakuda-stack/Dream-Chatbox-Plugins)")
RETRY_AFTER = 600.0          # a failed lookup is tried again after 10 min


class WorldCapacity:
    def __init__(self, log=print):
        self.log = log
        self._lock = threading.Lock()
        self._known = {}          # world id -> capacity (int)
        self._failed = {}         # world id -> time of the failure
        self._busy = set()

    def get(self, world_id):
        """The capacity when known, else None - and a lookup is started
        in the background, so the next frame has it."""
        if not world_id or not world_id.startswith("wrld_"):
            return None
        with self._lock:
            if world_id in self._known:
                return self._known[world_id]
            failed = self._failed.get(world_id)
            if world_id in self._busy or (
                    failed and time.time() - failed < RETRY_AFTER):
                return None
            self._busy.add(world_id)
        threading.Thread(target=self._fetch, args=(world_id,),
                         name="world_stats-capacity", daemon=True).start()
        return None

    def _fetch(self, world_id):
        try:
            req = urllib.request.Request(API + world_id, headers={
                "User-Agent": USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
            capacity = int(data.get("capacity") or 0) or None
            with self._lock:
                if capacity:
                    self._known[world_id] = capacity
                else:
                    self._failed[world_id] = time.time()
        except Exception as e:
            with self._lock:
                self._failed[world_id] = time.time()
            self.log(f"world capacity for {world_id} not available: {e}")
        finally:
            with self._lock:
                self._busy.discard(world_id)
