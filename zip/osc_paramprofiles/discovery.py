"""The other half of OSCQuery: reading VRChat's own service.

Announcing ourselves gets VRChat to *push* parameter changes. That is
only half the protocol, and the half that causes every "nothing arrives"
report - a push only happens when something changes, so a listener that
starts mid-session sees an empty world until the user moves a slider.

VRChat also *runs* an OSCQuery server. Browsing mDNS for it gives three
things that were previously guessed:

    HOST_INFO     which port VRChat actually listens on, instead of
                  assuming 9000 and being wrong after a launch argument
    the tree      every avatar parameter with its current value, on
                  demand, without waiting for a change
    TYPE/ACCESS   the authoritative type, and whether VRChat will accept
                  a write at all

So this module turns "hope a packet arrives" into "ask". The UDP socket
in oscio.py stays for live changes; this is the ground truth underneath
it.

No Qt, no globals - one object, owned by main.py.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import socket
import threading
import time
import urllib.error
import urllib.request

PARAM_PREFIX = "/avatar/parameters/"

#: VRChat names its service VRChat-Client-XXXXXX. Matching on the prefix
#: keeps us from talking to our own advertisement, or to VRCX, or to any
#: of the half dozen other OSCQuery tools a VR user has running.
VRC_SERVICE_PREFIX = "vrchat-client"

ACCESS_WRITE = 2


def zeroconf_available():
    try:
        import zeroconf  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------- shared zeroconf

_zc_lock = threading.Lock()
_zc = None
_zc_users = 0


def acquire_zeroconf():
    """One Zeroconf instance for the whole plugin, refcounted.

    Every Zeroconf() binds its own multicast sockets and starts its own
    threads. Creating one to announce, another to browse, and a fresh
    pair on every reconnect is a large part of why enabling the plugin
    stuttered and why reconnecting hung.
    """
    global _zc, _zc_users
    with _zc_lock:
        if _zc is None:
            try:
                from zeroconf import Zeroconf
                _zc = Zeroconf()
            except Exception:
                return None
        _zc_users += 1
        return _zc


def release_zeroconf():
    global _zc, _zc_users
    with _zc_lock:
        _zc_users = max(0, _zc_users - 1)
        if _zc_users == 0 and _zc is not None:
            try:
                _zc.close()
            except Exception:
                pass
            _zc = None


class VrcService:
    """What we found: where VRChat's HTTP server is, and where its OSC
    input is. Both can change when VRChat restarts."""

    __slots__ = ("host", "http_port", "osc_ip", "osc_port", "name", "seen")

    def __init__(self, host, http_port, name=""):
        self.host = host
        self.http_port = http_port
        self.name = name
        self.osc_ip = ""
        self.osc_port = 0
        self.seen = time.monotonic()

    @property
    def base(self):
        return f"http://{self.host}:{self.http_port}"

    def __repr__(self):
        return f"<VrcService {self.name} http={self.base} osc={self.osc_port}>"


class Discovery:
    """Browses mDNS for VRChat and reads its OSCQuery tree.

    Every public method is safe to call from any thread. The zeroconf
    callbacks land on zeroconf's own thread, HTTP happens on whichever
    thread asked, and ``_lock`` guards the little bit of shared state
    between them.
    """

    def __init__(self, log=None):
        self._log = log or (lambda *a: None)
        self._lock = threading.RLock()
        self._service = None
        self._zc = None
        self._browser = None
        self.error = ""
        self.last_fetch = 0.0
        self.last_fetch_count = 0

    # ------------------------------------------------------- lifecycle
    def start(self):
        """Begin browsing. Uses the shared Zeroconf, so this no longer
        costs a second multicast stack."""
        self.stop()
        self.error = ""
        try:
            from zeroconf import ServiceBrowser
        except ImportError:
            self.error = ("python-zeroconf is missing - VRChat cannot be "
                          "discovered")
            self._log(self.error)
            return False

        outer = self

        class Listener:
            def add_service(self, zc, stype, name):
                outer._resolve(zc, stype, name)

            def update_service(self, zc, stype, name):
                outer._resolve(zc, stype, name)

            def remove_service(self, _zc, _stype, name):
                with outer._lock:
                    if (outer._service is not None
                            and outer._service.name == name):
                        outer._log(f"VRChat's OSCQuery service went away "
                                   f"({name})")
                        outer._service = None

        self._zc = acquire_zeroconf()
        if self._zc is None:
            self.error = "could not start mDNS"
            self._log(self.error)
            return False
        try:
            self._browser = ServiceBrowser(self._zc, "_oscjson._tcp.local.",
                                           Listener())
            return True
        except Exception as exc:
            self.error = f"mDNS browse failed: {exc}"
            self._log(self.error)
            release_zeroconf()
            self._zc = None
            return False

    def stop(self):
        if self._browser is not None:
            try:
                self._browser.cancel()
            except Exception:
                pass
            self._browser = None
        if self._zc is not None:
            release_zeroconf()
            self._zc = None
        with self._lock:
            self._service = None

    # --------------------------------------------------------- resolve
    def _resolve(self, zc, stype, name):
        if VRC_SERVICE_PREFIX not in name.lower():
            return
        try:
            # blocks zeroconf's dispatch thread, so keep it short
            info = zc.get_service_info(stype, name, timeout=1200)
        except Exception:
            return
        if info is None or not info.port:
            return
        host = ""
        for raw in (info.addresses or []):
            try:
                candidate = socket.inet_ntoa(raw)
            except OSError:
                continue
            host = candidate
            if candidate.startswith("127."):
                break  # localhost wins - VRChat is on this machine
        if not host:
            host = "127.0.0.1"

        service = VrcService(host, info.port, name)
        with self._lock:
            known = self._service
            if (known is not None and known.host == host
                    and known.http_port == info.port):
                known.seen = time.monotonic()
                return
            self._service = service
        self._log(f"found VRChat OSCQuery at {service.base}")
        self.read_host_info()

    # ------------------------------------------------------- http side
    def _get(self, path, timeout=2.0):
        with self._lock:
            service = self._service
        if service is None:
            return None
        try:
            with urllib.request.urlopen(service.base + path,
                                        timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, OSError, ValueError,
                json.JSONDecodeError) as exc:
            self.error = f"{path}: {exc}"
            return None

    def read_host_info(self):
        """Ask VRChat which port it wants OSC on, rather than assuming."""
        data = self._get("/?HOST_INFO")
        if not isinstance(data, dict):
            return False
        with self._lock:
            service = self._service
            if service is None:
                return False
            service.osc_ip = str(data.get("OSC_IP") or "127.0.0.1")
            try:
                service.osc_port = int(data.get("OSC_PORT") or 0)
            except (TypeError, ValueError):
                service.osc_port = 0
            port = service.osc_port
        if port:
            self._log(f"VRChat accepts OSC on {port}")
        return bool(port)

    def fetch_avatar(self):
        """The current avatar id, pulled rather than waited for.

        Without this the id is only known after VRChat happens to push
        an /avatar/change - so a plugin started mid-session had no idea
        which avatar was on, and every "was this captured on another
        avatar?" check silently passed.
        """
        data = self._get("/avatar/change")
        if not isinstance(data, dict):
            return ""
        values = data.get("VALUE")
        if isinstance(values, list) and values:
            return str(values[0] or "")
        return ""

    def fetch_parameters(self):
        """The whole avatar parameter tree, right now.

        Returns {name: (value, tag, writable)} or None when VRChat is not
        reachable. This is what makes "+ New profile" work the second the
        plugin is switched on instead of after the next avatar change.
        """
        data = self._get(PARAM_PREFIX.rstrip("/"))
        if data is None:
            # older builds only answer on the root
            root = self._get("/")
            if not isinstance(root, dict):
                return None
            node = root
            for part in ("avatar", "parameters"):
                node = (node.get("CONTENTS") or {}).get(part) or {}
            data = node
        if not isinstance(data, dict):
            return None

        out = {}
        self._walk(data, out)
        self.last_fetch = time.time()
        self.last_fetch_count = len(out)
        return out

    def _walk(self, node, out):
        """OSCQuery trees nest, and VRChat nests them for parameters with
        a slash in the name. Recursing means those are not silently lost.
        """
        path = str(node.get("FULL_PATH") or "")
        contents = node.get("CONTENTS")
        if isinstance(contents, dict):
            for child in contents.values():
                if isinstance(child, dict):
                    self._walk(child, out)
            return
        if not path.startswith(PARAM_PREFIX):
            return
        name = path[len(PARAM_PREFIX):]
        if not name:
            return

        tag = self._tag(node.get("TYPE"))
        values = node.get("VALUE")
        value = values[0] if isinstance(values, list) and values else None
        if value is None:
            return
        if tag == "b":
            value = bool(value) if not isinstance(value, str) else \
                value.strip().lower() in ("1", "true")
        elif tag == "i":
            try:
                value = int(value)
            except (TypeError, ValueError):
                return
        elif tag == "f":
            try:
                value = float(value)
            except (TypeError, ValueError):
                return

        try:
            access = int(node.get("ACCESS", 3))
        except (TypeError, ValueError):
            access = 3
        out[name] = (value, tag, bool(access & ACCESS_WRITE))

    @staticmethod
    def _tag(type_string):
        text = str(type_string or "f")
        if text and text[0] in ("T", "F"):
            return "b"
        if text and text[0] == "i":
            return "i"
        if text and text[0] == "f":
            return "f"
        if text and text[0] == "s":
            return "s"
        return "f"

    # ----------------------------------------------------------- state
    @property
    def service(self):
        with self._lock:
            return self._service

    def send_target(self, fallback_host="127.0.0.1", fallback_port=9000):
        """Where to send. Discovered if we know, the old defaults if not -
        so a first Load still works while mDNS is still waking up."""
        with self._lock:
            service = self._service
        if service is not None and service.osc_port:
            host = service.osc_ip or service.host or fallback_host
            if host in ("0.0.0.0", ""):
                host = service.host or fallback_host
            return host, service.osc_port, True
        return fallback_host, fallback_port, False

    def status(self):
        with self._lock:
            service = self._service
        return {
            "found": service is not None,
            "name": service.name.split(".")[0] if service else "",
            "http": service.base if service else "",
            "osc_port": service.osc_port if service else 0,
            "error": self.error,
            "last_fetch": self.last_fetch,
            "last_fetch_count": self.last_fetch_count,
        }
