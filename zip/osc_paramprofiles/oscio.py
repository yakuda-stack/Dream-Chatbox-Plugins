"""OSC plumbing: codec, UDP socket, our own OSCQuery service.

Dependency free apart from zeroconf, which is required rather than
optional now: OSCQuery is the only transport the plugin uses, and
without mDNS there is nothing to announce ourselves to.

The codec is ~120 lines of struct packing covering exactly the subset
VRChat speaks. That is cheaper than depending on python-osc being
importable from inside a plugin, and it behaves the same on the
AppImage, the AUR package and a bare `python -m`.

Nothing here imports Qt. The panel polls this module; this module never
calls into the panel.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import socket
import struct
import threading
import time

# --------------------------------------------------------------- codec


def _pad(n):
    return (4 - (n % 4)) % 4


def _ostr(text):
    raw = str(text).encode("utf-8") + b"\x00"
    return raw + b"\x00" * _pad(len(raw))


def _rstr(data, i):
    end = data.index(b"\x00", i)
    text = data[i:end].decode("utf-8", "replace")
    size = end - i + 1
    return text, i + size + _pad(size)


def encode(address, args=()):
    """Build one OSC message. bool is checked before int on purpose -
    in python True *is* an int and VRChat wants the T/F typetag."""
    tags = ","
    body = b""
    for arg in args:
        if isinstance(arg, bool):
            tags += "T" if arg else "F"
        elif isinstance(arg, int):
            tags += "i"
            body += struct.pack(">i", max(-2147483648, min(2147483647, arg)))
        elif isinstance(arg, float):
            tags += "f"
            body += struct.pack(">f", arg)
        else:
            tags += "s"
            body += _ostr(arg)
    return _ostr(address) + _ostr(tags) + body


def decode(data):
    """Yield (address, args) for a packet. Bundles recurse, so a bundled
    avatar dump comes out as a flat stream of messages."""
    if data[:8] == b"#bundle\x00":
        i = 16
        while i + 4 <= len(data):
            size = struct.unpack_from(">i", data, i)[0]
            i += 4
            if size < 0 or i + size > len(data):
                return
            for item in decode(data[i:i + size]):
                yield item
            i += size
        return
    try:
        address, i = _rstr(data, 0)
        if i >= len(data):
            yield address, []
            return
        tags, i = _rstr(data, i)
        args = []
        for tag in tags[1:]:
            if tag == "i":
                args.append(struct.unpack_from(">i", data, i)[0])
                i += 4
            elif tag == "f":
                args.append(struct.unpack_from(">f", data, i)[0])
                i += 4
            elif tag == "d":
                args.append(struct.unpack_from(">d", data, i)[0])
                i += 8
            elif tag in ("s", "S"):
                text, i = _rstr(data, i)
                args.append(text)
            elif tag == "T":
                args.append(True)
            elif tag == "F":
                args.append(False)
            elif tag == "N":
                args.append(None)
            elif tag == "b":
                size = struct.unpack_from(">i", data, i)[0]
                i += 4 + size + _pad(size)
            else:
                break
        yield address, args
    except (ValueError, struct.error, IndexError):
        return


# ------------------------------------------------------- VRChat naming

#: Parameters VRChat drives itself. VRChat's own OSCQuery tree is the
#: better source for this - ACCESS says whether a write is accepted - so
#: this list is now only the fallback for parameters that arrived over
#: UDP before the tree was ever fetched.
VRC_BUILTIN = frozenset((
    "IsLocal", "PreviewMode", "Viseme", "Voice",
    "GestureLeft", "GestureRight", "GestureLeftWeight", "GestureRightWeight",
    "AngularY", "VelocityX", "VelocityY", "VelocityZ", "VelocityMagnitude",
    "Upright", "Grounded", "Seated", "AFK", "Expression1", "Expression2",
    "TrackingType", "VRMode", "MuteSelf", "InStation", "Earmuffs",
    "IsOnFriendsList", "AvatarVersion", "ScaleModified", "ScaleFactor",
    "ScaleFactorInverse", "EyeHeightAsMeters", "EyeHeightAsPercent",
    "IsAnimatorEnabled", "Face",
))

#: PhysBone and Contact receivers append these. Written by the physics
#: solver every frame, so a saved value is meaningless.
VRC_DRIVEN_SUFFIX = ("_IsGrabbed", "_IsPosed", "_Angle", "_Stretch",
                     "_Squish", "_Distance", "_Proximity")

PARAM_PREFIX = "/avatar/parameters/"


def is_named_driven(name, skip_builtin=True, skip_driven=True):
    """Name-based guess, used only where VRChat has not told us."""
    if skip_builtin and name in VRC_BUILTIN:
        return True
    if skip_driven and name.endswith(VRC_DRIVEN_SUFFIX):
        return True
    return False


def type_of(value):
    if isinstance(value, bool):
        return "b"
    if isinstance(value, int):
        return "i"
    if isinstance(value, float):
        return "f"
    return "s"


def cast(value, tag):
    """Bring a value stored in json back to the type the tag promises."""
    try:
        if tag == "b":
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        if tag == "i":
            return int(float(value))
        if tag == "f":
            return float(value)
    except (TypeError, ValueError):
        return value
    return str(value)


# -------------------------------------------------------------- bridge


class Bridge:
    """One UDP socket, one receive thread, one advertised OSCQuery
    service. There is no second mode any more: the port is always chosen
    by the OS and always announced, so the plugin can never collide with
    the app's own receiver on 9001.

    Thread safety: ``_lock`` guards the parameter table. Sending is
    lock-free - a UDP socket may be written from any thread - so the
    apply worker never blocks the receive thread.
    """

    def __init__(self, log=None):
        self._log = log or (lambda *a: None)
        self._lock = threading.RLock()
        self._params = {}          # name -> (value, tag, monotonic stamp)
        self._writable = {}        # name -> bool, from VRChat's ACCESS
        self._avatar = ""
        self._last_rx = 0.0
        self._packets = 0

        self._sock = None
        self._rx = None
        self._stop = threading.Event()

        self._http = None
        self._http_thread = None
        self._zc = None
        self._zc_info = []

        self.listen_port = 0
        self.http_port = 0
        self.send_host = "127.0.0.1"
        self.send_port = 9000
        self.send_discovered = False
        self.service_name = "OSC-DreamChatbox ParamProfiles"
        self.error = ""
        self.announced = False
        self.announced_at = 0.0
        #: True while a (slow) registration is in flight. Without this
        #: the panel reads announced=False mid-reannounce and reports a
        #: failure that has not happened.
        self.announcing = False

    # ------------------------------------------------------- lifecycle
    def open_socket(self):
        """Bind and start receiving. Instant - safe on the GUI thread.

        Deliberately split from announce(): registering an mDNS service
        blocks for seconds while zeroconf probes for a name conflict, and
        doing that inside setup() froze the whole app every time the
        plugin was switched on.
        """
        self.error = ""
        self._stop.clear()
        if self._sock is not None:
            return True
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
            # 0.0.0.0 rather than 127.0.0.1: VRChat runs under Proton and
            # its idea of "localhost" has been known not to be ours.
            sock.bind(("0.0.0.0", 0))
            sock.settimeout(0.4)
        except OSError as exc:
            self.error = f"could not open a socket: {exc.strerror or exc}"
            self._log(self.error)
            return False

        self._sock = sock
        self.listen_port = sock.getsockname()[1]
        self._rx = threading.Thread(target=self._recv_loop,
                                    name="paramprofiles-rx", daemon=True)
        self._rx.start()
        self._log(f"listening on udp/{self.listen_port}")
        return True

    def announce(self, service_name=None):
        """Publish the OSCQuery service. Slow - call from a thread."""
        if service_name:
            self.service_name = service_name
        if self._sock is None and not self.open_socket():
            return False
        if not self._start_http():
            return False
        return self._register()

    def stop(self):
        self._stop.set()
        self.withdraw()
        self.shutdown_http()
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        thread, self._rx = self._rx, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.5)

    def reannounce(self):
        """Withdraw and re-register. VRChat treats the reappearing
        service as a new listener, re-reads our node tree and pushes the
        full parameter set. Slow - call from a thread."""
        self.withdraw(keep_announcing=True)
        return self._register()

    # --------------------------------------------------------- receive
    def _recv_loop(self):
        sock = self._sock
        while not self._stop.is_set() and sock is not None:
            try:
                data, _addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            self._packets += 1
            for address, args in decode(data):
                self.ingest(address, args)

    def ingest(self, address, args):
        """One OSC message in, from the socket."""
        now = time.monotonic()
        if address == "/avatar/change":
            with self._lock:
                self._avatar = str(args[0]) if args else ""
                self._params.clear()
                self._writable.clear()
                self._last_rx = now
            return
        if not address.startswith(PARAM_PREFIX):
            return
        name = address[len(PARAM_PREFIX):]
        if not name or not args or args[0] is None:
            return
        value = args[0]
        with self._lock:
            self._params[name] = (value, type_of(value), now)
            self._last_rx = now

    #: A push that landed this long before the fetch began still counts
    #: as newer than the response body. VRChat renders the body at some
    #: unknown point between the request and the reply, so without a
    #: little slack a poll can undo a toggle the user flipped a
    #: heartbeat earlier - which looks exactly like the toggle bouncing
    #: back on its own.
    PUSH_GRACE = 0.75

    def ingest_tree(self, parameters, started_at):
        """Merge a snapshot fetched from VRChat's OSCQuery server.

        ``started_at`` is the monotonic time the fetch began. Anything
        that arrived over UDP since then - minus the grace above - is
        newer than what the response describes, so it wins.
        """
        if not parameters:
            return 0
        cutoff = started_at - self.PUSH_GRACE
        merged = 0
        with self._lock:
            for name, (value, tag, writable) in parameters.items():
                self._writable[name] = writable
                known = self._params.get(name)
                if known is not None and known[2] > cutoff:
                    continue
                self._params[name] = (value, tag, started_at)
                merged += 1
            if merged:
                self._last_rx = time.monotonic()
        return merged

    def set_avatar(self, avatar_id):
        with self._lock:
            self._avatar = str(avatar_id or "")

    # ----------------------------------------------------------- reads
    def snapshot(self):
        """{name: value}, safe to hand to the GUI thread."""
        with self._lock:
            return {k: v[0] for k, v in self._params.items()}

    def detailed(self):
        """{name: (value, tag, writable_or_None)} - all the UI needs."""
        with self._lock:
            return {k: (v[0], v[1], self._writable.get(k))
                    for k, v in self._params.items()}

    def is_writable(self, name, skip_builtin=True, skip_driven=True):
        """VRChat's own answer when we have it, the name-based guess when
        we do not. Layered on purpose: ACCESS is authoritative but only
        exists once the tree has been fetched."""
        with self._lock:
            known = self._writable.get(name)
        if known is not None:
            return known
        return not is_named_driven(name, skip_builtin, skip_driven)

    @property
    def avatar(self):
        with self._lock:
            return self._avatar

    @property
    def count(self):
        with self._lock:
            return len(self._params)

    @property
    def has_tree(self):
        with self._lock:
            return bool(self._writable)

    def status(self):
        with self._lock:
            age = time.monotonic() - self._last_rx if self._last_rx else -1.0
            return {
                "params": len(self._params),
                "avatar": self._avatar,
                "age": age,
                "packets": self._packets,
                "listen": self.listen_port,
                "http": self.http_port,
                "announced": self.announced,
                "announcing": self.announcing,
                "error": self.error,
                "from_tree": len(self._writable),
            }

    # ------------------------------------------------------------ send
    def set_target(self, host, port, discovered=False):
        self.send_host = host or "127.0.0.1"
        self.send_port = int(port or 9000)
        self.send_discovered = bool(discovered)

    def send(self, name, value):
        sock = self._sock
        if sock is None:
            return False
        try:
            sock.sendto(encode(PARAM_PREFIX + name, [value]),
                        (self.send_host, self.send_port))
            return True
        except OSError as exc:
            self.error = str(exc)
            return False

    # ------------------------------------------- our OSCQuery service
    def _tree(self):
        """What we advertise. Listing the parameters we already know
        keeps VRChat happy when it inspects us back."""
        leaf = {"FULL_PATH": "/avatar/parameters", "ACCESS": 3,
                "DESCRIPTION": "avatar parameters", "CONTENTS": {}}
        with self._lock:
            known = dict(self._params)
        for name, (value, tag, _stamp) in sorted(known.items()):
            leaf["CONTENTS"][name] = {
                "FULL_PATH": PARAM_PREFIX + name,
                "ACCESS": 3,
                "TYPE": {"b": "T", "i": "i", "f": "f"}.get(tag, "s"),
                "VALUE": [value],
            }
        return {
            "DESCRIPTION": "root node",
            "FULL_PATH": "/",
            "ACCESS": 0,
            "CONTENTS": {
                "avatar": {
                    "FULL_PATH": "/avatar",
                    "ACCESS": 0,
                    "CONTENTS": {
                        "change": {"FULL_PATH": "/avatar/change",
                                   "ACCESS": 3, "TYPE": "s", "VALUE": [""]},
                        "parameters": leaf,
                    },
                },
            },
        }

    def _host_info(self):
        return {
            "NAME": self.service_name,
            "OSC_IP": "127.0.0.1",
            "OSC_PORT": self.listen_port,
            "OSC_TRANSPORT": "UDP",
            "EXTENSIONS": {"ACCESS": True, "VALUE": True, "TYPE": True,
                           "RANGE": False, "CLIPMODE": False,
                           "DESCRIPTION": True},
        }

    def _announce(self):
        if not self._start_http():
            return False
        return self._register()

    def _start_http(self):
        if self._http is not None:
            return True
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        bridge = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                pass  # the app has its own log

            def do_GET(self):  # noqa: N802 - stdlib naming
                path, _, query = self.path.partition("?")
                if "HOST_INFO" in query:
                    payload = bridge._host_info()
                else:
                    node = bridge._tree()
                    for part in [p for p in path.split("/") if p]:
                        node = (node.get("CONTENTS") or {}).get(part)
                        if node is None:
                            self.send_error(404)
                            return
                    payload = node
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        try:
            self._http = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
            self._http.daemon_threads = True
            self.http_port = self._http.server_address[1]
            self._http_thread = threading.Thread(
                target=self._http.serve_forever, kwargs={"poll_interval": 0.5},
                name="paramprofiles-http", daemon=True)
            self._http_thread.start()
            return True
        except OSError as exc:
            self.error = f"OSCQuery http server: {exc}"
            self._log(self.error)
            return False

    def _register(self):
        self.announcing = True
        try:
            return self._register_inner()
        finally:
            self.announcing = False

    def _register_inner(self):
        try:
            from zeroconf import ServiceInfo
        except ImportError:
            self.error = ("python-zeroconf is not installed - VRChat cannot "
                          "discover this plugin")
            self._log(self.error)
            return False
        from . import discovery
        try:
            # One Zeroconf instance for the whole plugin. Three of them -
            # one to announce, one to browse, one more per reconnect -
            # each bind their own multicast sockets and threads, which is
            # a large part of why switching the plugin on stuttered.
            self._zc = discovery.acquire_zeroconf()
            if self._zc is None:
                self.error = "could not start mDNS"
                return False
            host = socket.gethostname().split(".")[0] or "localhost"
            safe = self.service_name.replace(".", " ")[:48]
            addr = socket.inet_aton("127.0.0.1")
            for stype, port in (("_oscjson._tcp.local.", self.http_port),
                                ("_osc._udp.local.", self.listen_port)):
                info = ServiceInfo(
                    stype, f"{safe}.{stype}", addresses=[addr], port=port,
                    properties={}, server=f"{host}-paramprofiles.local.")
                # cooperating_responders skips the conflict-probe wait,
                # which is what avahi is already doing for us on Arch.
                try:
                    self._zc.register_service(info, allow_name_change=True,
                                              cooperating_responders=True)
                except TypeError:  # older python-zeroconf
                    self._zc.register_service(info, allow_name_change=True)
                self._zc_info.append(info)
            self.announced = True
            self.announced_at = time.monotonic()
            self._log(f"announced over mDNS (http tcp/{self.http_port}, "
                      f"osc udp/{self.listen_port})")
            return True
        except Exception as exc:  # zeroconf raises a zoo of its own
            self.error = f"mDNS registration failed: {exc}"
            self._log(self.error)
            return False

    def withdraw(self, keep_announcing=False):
        """Take the service off the network. Slow - call from a thread."""
        self.announcing = bool(keep_announcing)
        for info in self._zc_info:
            try:
                self._zc.unregister_service(info)
            except Exception:
                pass
        self._zc_info = []
        if self._zc is not None:
            from . import discovery
            discovery.release_zeroconf()
            self._zc = None
        self.announced = False

    def shutdown_http(self):
        if self._http is not None:
            try:
                self._http.shutdown()
                self._http.server_close()
            except Exception:
                pass
            self._http = None
            self.http_port = 0
