"""
vrchatlog.py – reads VRChat's output_log to expose live world info
(bundled with the World Stats plugin; the app itself no longer ships this)

VRChat does NOT send the current world name or the number of players in
the instance over OSC. The only source available offline on Linux is the
game's own text log (`output_log_*.txt`). This is exactly what the
Windows tools (MagicChatbox, VRCX ...) parse too.

Under Proton the log lives inside the Steam prefix, e.g.

    ~/.local/share/Steam/steamapps/compatdata/438100/pfx/drive_c/users/
        steamuser/AppData/LocalLow/VRChat/VRChat/output_log_*.txt

We resolve the folder from a couple of well-known Steam locations (native
+ Flatpak + extra library folders from libraryfolders.vdf), or from a
manual override. The newest `output_log_*.txt` is the running session.

What we parse (all lines carry a `[Behaviour]` tag):
    - "Joining wrld_…:<instanceId>~<tokens>"  -> new instance: reset the
      player set + derive the instance/access type (Public, Friends,
      Group, Group+, Invite …)
    - "Joining or Creating Room: <name>"      -> the human world name
    - "OnPlayerJoined <name>"                 -> +1 player (incl. yourself)
    - "OnPlayerLeft <name>"                   -> -1 player
    - "OnLeftRoom" / "Successfully left room" -> left the instance

Each line also carries a timestamp, which is where the two session
counters come from: the join line gives the moment you entered the
instance, the very first line of the file gives the moment VRChat was
launched. Both are read out of the log rather than measured with our
own clock, so restarting the chatbox in the middle of a session does
not reset them.

Everything runs in a daemon thread (log files get large; we never touch
the file from the GUI thread). The GUI reads a cheap snapshot() under a
lock. Reading is incremental – only the bytes appended since the last
poll are parsed, so it stays light even during long sessions.

No extra dependencies – stdlib only.
"""

import re
import threading
import os
import platform
import time
from pathlib import Path

VRCHAT_APPID = "438100"

# folder inside a Steam prefix where VRChat drops its logs
_PREFIX_TAIL = Path(
    "pfx/drive_c/users/steamuser/AppData/LocalLow/VRChat/VRChat")

# Steam roots to probe (native + common variants + Flatpak)
IS_WINDOWS = platform.system() == "Windows"

# On Windows VRChat writes straight into the user profile - no Proton
# prefix to hunt for. LOCALAPPDATA points at ...\AppData\Local, and the
# "Low" folder sits next to it, which is why this is not simply
# LOCALAPPDATA/VRChat.
def _windows_log_dirs():
    dirs = []
    home = Path.home()
    dirs.append(home / "AppData" / "LocalLow" / "VRChat" / "VRChat")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(Path(local).parent / "LocalLow" / "VRChat" / "VRChat")
    profile = os.environ.get("USERPROFILE")
    if profile:
        dirs.append(Path(profile) / "AppData" / "LocalLow" / "VRChat"
                    / "VRChat")
    return dirs


_STEAM_ROOTS = [
    Path.home() / ".local/share/Steam",
    Path.home() / ".steam/steam",
    Path.home() / ".steam/root",
    Path.home() / ".var/app/com.valvesoftware.Steam/data/Steam",
]

# ---------------------------------------------------------------- log regex
_RE_JOIN_INSTANCE = re.compile(r"Joining (wrld_[^\s:]+):(\S+)")
_RE_JOIN_ROOM = re.compile(r"Joining or Creating Room:\s*(.+?)\s*$")
_RE_PLAYER_JOIN = re.compile(
    r"OnPlayerJoined\s+(.+?)(?:\s+\(usr_[0-9a-fA-F-]+\))?\s*$")
_RE_PLAYER_LEFT = re.compile(
    r"OnPlayerLeft\s+(.+?)(?:\s+\(usr_[0-9a-fA-F-]+\))?\s*$")
_RE_LEFT_ROOM = re.compile(r"OnLeftRoom|Successfully left room")

# every log line starts with "2026.08.11 05:12:33 Log        -  ..."
_RE_TIMESTAMP = re.compile(
    r"^(\d{4})\.(\d{2})\.(\d{2})\s+(\d{2}):(\d{2}):(\d{2})")


def _line_time(line: str) -> float:
    """Epoch seconds of a log line, 0.0 when it carries no timestamp.

    Taken from the line rather than from the clock so that attaching to
    a log half way through a session still reports the real elapsed
    time - restart the app mid-session and the counters do not reset.
    VRChat writes local time, which is what mktime expects."""
    m = _RE_TIMESTAMP.match(line)
    if not m:
        return 0.0
    y, mo, d, h, mi, s = (int(g) for g in m.groups())
    try:
        return time.mktime((y, mo, d, h, mi, s, 0, 0, -1))
    except Exception:
        return 0.0


def _instance_type(descriptor: str) -> str:
    """Human name for the access type of an instance descriptor like
    '12345~group(grp_x)~groupAccessType(members)~region(use)'."""
    d = descriptor
    if "~group(" in d or "~groupAccessType(" in d:
        if "groupAccessType(public)" in d:
            return "Group Public"
        if "groupAccessType(plus)" in d:
            return "Group+"
        return "Group"
    if "~hidden(" in d:
        return "Friends+"
    if "~friends(" in d:
        return "Friends"
    if "~private(" in d:
        return "Invite+" if "~canRequestInvite" in d else "Invite"
    return "Public"


class VRChatLogWatcher:
    """Live world/player info from VRChat's output log.

    Start it once (start()); it keeps a background thread that tails the
    newest log file. Read the current state via snapshot(). Set an
    explicit folder with set_override() (empty = auto-detect)."""

    def __init__(self, log_fn=print):
        self.log = log_fn
        self._lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()
        self._override = ""

        # parsed state (guarded by _lock)
        self._world = ""
        self._itype = ""
        self._players = set()
        self._in_world = False
        self._joined_at = 0.0        # instance join, from the log clock
        self._session_start = 0.0    # first line of this log = VRChat start

        # file tracking
        self._cur_path = None
        self._offset = 0
        self._warned = False

    # ----------------------------------------------------------- lifecycle
    def set_override(self, path: str):
        """Manual log folder ('' = auto-detect). Forces a re-attach."""
        with self._lock:
            self._override = (path or "").strip()
            self._cur_path = None
            self._offset = 0
            self._warned = False

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    @property
    def running(self):
        return bool(self._thread and self._thread.is_alive())

    def snapshot(self):
        """Current state as a dict. Cheap; safe from the GUI thread."""
        with self._lock:
            return {
                "in_world": self._in_world,
                "world": self._world,
                "instance_type": self._itype,
                "player_count": len(self._players),
                "joined_at": self._joined_at,
                "session_start": self._session_start,
                "log_dir": (str(self._cur_path.parent)
                            if self._cur_path else ""),
            }

    # ----------------------------------------------------------- discovery
    def _library_roots(self):
        if IS_WINDOWS:
            # Steam libraries only matter for the Proton prefix hunt
            return []
        """Extra Steam library folders parsed from libraryfolders.vdf –
        VRChat may live on a different drive than the main Steam root."""
        roots = []
        for base in _STEAM_ROOTS:
            vdf = base / "steamapps" / "libraryfolders.vdf"
            try:
                txt = vdf.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for m in re.finditer(r'"path"\s*"([^"]+)"', txt):
                roots.append(Path(m.group(1).replace("\\\\", "/")))
        return roots

    def _find_log_dir(self):
        """Resolve the VRChat log folder (override wins, else probe)."""
        if self._override:
            p = Path(self._override).expanduser()
            return p if p.is_dir() else None
        candidates = []
        if IS_WINDOWS:
            candidates.extend(_windows_log_dirs())
        for base in _STEAM_ROOTS + self._library_roots():
            candidates.append(
                base / "steamapps" / "compatdata" / VRCHAT_APPID
                / _PREFIX_TAIL)
        seen = set()
        for c in candidates:
            key = str(c)
            if key in seen:
                continue
            seen.add(key)
            if c.is_dir():
                return c
        return None

    def _newest_log(self, folder: Path):
        try:
            logs = [p for p in folder.glob("output_log_*.txt")]
        except Exception:
            return None
        if not logs:
            return None
        return max(logs, key=lambda p: p.stat().st_mtime)

    # ----------------------------------------------------------------- run
    def _run(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                if not self._warned:
                    self.log(f"VRChat log: read error – {e}")
                    self._warned = True
            self._stop.wait(2.0)

    def _tick(self):
        folder = self._find_log_dir()
        if folder is None:
            if not self._warned:
                where = ("is VRChat installed?" if IS_WINDOWS
                         else "is VRChat installed via Steam/Proton?")
                self.log(f"VRChat log: no output_log folder found ({where}) "
                         f"- set the folder manually in the plugin settings.")
                self._warned = True
            with self._lock:
                self._in_world = False
            return

        newest = self._newest_log(folder)
        if newest is None:
            with self._lock:
                self._in_world = False
            return

        # new session / rotated file -> restart from the top
        if newest != self._cur_path:
            self.log(f"VRChat log: reading {newest.name}")
            with self._lock:
                self._cur_path = newest
                self._offset = 0
                self._world = ""
                self._itype = ""
                self._players = set()
                self._in_world = False
                self._joined_at = 0.0
                self._session_start = 0.0
                self._warned = False

        try:
            size = newest.stat().st_size
        except Exception:
            return
        # file truncated/replaced under us -> re-read from start
        if size < self._offset:
            self._offset = 0
            with self._lock:
                self._players = set()

        if size == self._offset:
            return

        with open(newest, "r", encoding="utf-8", errors="ignore") as f:
            f.seek(self._offset)
            chunk = f.read()
            self._offset = f.tell()

        for raw in chunk.splitlines():
            if not self._session_start:
                # the first timestamped line of the file is the moment
                # VRChat was launched - any line will do, not just the
                # [Behaviour] ones
                stamp = _line_time(raw)
                if stamp:
                    with self._lock:
                        self._session_start = stamp
            if "[Behaviour]" not in raw:
                continue
            self._parse_line(raw)

    def _parse_line(self, line: str):
        m = _RE_JOIN_INSTANCE.search(line)
        if m:
            with self._lock:
                self._players = set()
                self._itype = _instance_type(m.group(2))
                self._in_world = True
                self._joined_at = _line_time(line) or time.time()
            return
        m = _RE_JOIN_ROOM.search(line)
        if m:
            with self._lock:
                self._world = m.group(1).strip()
                self._in_world = True
                if not self._joined_at:
                    self._joined_at = _line_time(line) or time.time()
            return
        m = _RE_PLAYER_JOIN.search(line)
        if m:
            with self._lock:
                self._players.add(m.group(1).strip())
            return
        m = _RE_PLAYER_LEFT.search(line)
        if m:
            with self._lock:
                self._players.discard(m.group(1).strip())
            return
        if _RE_LEFT_ROOM.search(line):
            with self._lock:
                self._players = set()
                self._in_world = False
                self._world = ""
                self._itype = ""
                self._joined_at = 0.0
