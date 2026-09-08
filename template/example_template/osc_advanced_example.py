"""
osc_advanced_example.py - a plugin with its own UDP port. INACTIVE.

===========================================================================
READ THIS BEFORE YOU COPY ANYTHING OUT OF HERE
===========================================================================

Nothing in this file is imported or executed. `main.py` does not touch
it, and deleting the file changes nothing. It is here as a worked
example for one narrow case, and the first thing it should tell you is
that the case is probably not yours.

**You do NOT need any of this if you want to:**

  - put text in the chatbox
      -> `get_text()` / `get_lines()` in main.py. The app owns the
         chatbox connection, rate limit and character budget.

  - read avatar parameters that VRChat sends
      -> the app already receives them. Switch **OSC input** on in
         Options and read what you need there; opening a second socket
         for the same stream means two programs competing for one
         thing VRChat only sends to one place.

  - send avatar parameters to VRChat
      -> the app already discovered the running client's real input
         port (which is often not 9000 - it differs as soon as two
         clients run, or VRChat was started with a custom --osc). It
         has a socket pointed at it.

For those three, a plugin that opens sockets is strictly worse than one
that does not: more moving parts, another thing to fail on somebody
else's machine, and a port conflict the user has to diagnose.

**You DO need this if something other than VRChat has to reach your
plugin at a port it was told about in advance.** That is the whole of
it. A fixed port only means anything when somebody outside is typing
the number in:

  - a hardware bridge or microcontroller sending OSC over the network
  - a heart-rate monitor app with a configurable OSC destination
  - OSCLeash-style setups where a second tool talks to the plugin
  - an avatar-stats reader that another program feeds

If you are not sure which side you are on, you are on the first one.

===========================================================================
WHAT THIS FILE DEMONSTRATES
===========================================================================

Four things, and each of them is where a first attempt usually goes
wrong:

1. Binding a port that may already be taken, and doing something
   sensible about it instead of dying or - worse - silently sharing it.
2. A receive thread that can be stopped, and the wait that makes a
   restart on the same port actually work.
3. Announcing the port over OSCQuery so VRChat finds it without the user
   typing anything.
4. Taking it all down again on teardown(), in an order that does not
   leave a thread holding a socket nobody owns any more.

To use it: drop the `_` prefix from the calls in main.py's setup() and
teardown() shown at the bottom, and add `zeroconf` to your plugin's
documented requirements if you want step 3.

===========================================================================
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# python-osc ships with the app, so this import is safe. zeroconf is
# optional: without it everything below still sends and receives, only
# the automatic discovery in step 3 is gone. Degrade, never refuse.
try:
    from pythonosc.osc_message_builder import OscMessageBuilder
    from pythonosc.osc_packet import OscPacket
    HAS_OSC = True
except ImportError:
    HAS_OSC = False

try:
    from zeroconf import IPVersion, ServiceInfo, Zeroconf
    HAS_ZEROCONF = True
except ImportError:
    HAS_ZEROCONF = False

OSCJSON_TYPE = "_oscjson._tcp.local."
OSC_TYPE = "_osc._udp.local."


class OwnOscPort:
    """One UDP socket, one receive thread, one announced OSCQuery
    service - the smallest thing that is actually correct.

    Thread model, because this is where it usually goes wrong:

      * `open()` and `close()` are called from the GUI thread, out of
        setup() and teardown(). Both must return quickly.
      * `_loop()` runs on its own thread and is the ONLY thing that
        touches the socket for reading.
      * `on_message` is called from that thread. **Never touch Qt in
        it** - not a label, not a list widget, nothing. Append to a
        plain list and let a QTimer in your panel read it, which is what
        panel.py does. `api.set()` is safe from any thread and is the
        intended way back into the app.
    """

    def __init__(self, api, on_message, port=0, announce=True,
                 service_name=None):
        self.api = api
        self.on_message = on_message
        self.wanted_port = int(port or 0)
        self.want_announce = bool(announce)
        self.service_name = service_name or f"DreamChatbox-{api.plugin_id}"

        self.port = 0
        self.error = ""
        self._sock = None
        self._thread = None
        self._running = False
        self._paths = {}

        self._http = None
        self._http_port = 0
        self._zc = None
        self._infos = []

    # ------------------------------------------------------------ open
    def open(self):
        """Bind and start receiving. Returns True when something is
        listening - which is not the same as "on the port you asked
        for", see below."""
        if not HAS_OSC:
            self.error = "python-osc is not installed"
            return False
        try:
            self._bind(self.wanted_port)
        except OSError as exc:
            if not self.wanted_port:
                # port 0 failing means the machine is out of sockets.
                # Nothing clever to do about that.
                self.error = str(exc)
                self.api.log(f"OSC: no socket ({exc})")
                return False
            # The wished-for port is taken - another OSC tool, or a
            # previous run of this app that has not let go yet. Falling
            # back keeps everything that discovers us through OSCQuery
            # working; only a sender that was handed the fixed number by
            # hand is out of luck, and it gets told the new one.
            self.api.log(f"OSC: udp/{self.wanted_port} is taken ({exc}) - "
                         f"falling back to an automatic port")
            try:
                self._bind(0)
            except OSError as exc2:
                self.error = str(exc2)
                self.api.log(f"OSC: no socket ({exc2})")
                return False

        self._running = True
        self._thread = threading.Thread(
            target=self._loop, name=f"{self.api.plugin_id}-osc", daemon=True)
        self._thread.start()
        self.api.log(f"OSC: listening on udp/{self.port}")
        if self.want_announce:
            self.announce()
        return True

    def _bind(self, port):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # NOTE the absence of SO_REUSEADDR. It is tempting - you will
        # see it in other OSC code - and it is wrong here: on a
        # dedicated port it lets two endpoints bind the same number and
        # quietly split the traffic between them. The symptom is "half
        # my messages never arrive", which is a miserable afternoon.
        # UDP has no TIME_WAIT, so nothing is gained by setting it, and
        # a clean failure is what makes the fallback above possible.
        #
        # 0.0.0.0 rather than 127.0.0.1: VRChat under Proton does not
        # always agree with us about what "localhost" means.
        sock.bind(("0.0.0.0", int(port)))
        # The timeout is what makes the thread stoppable. A blocking
        # recvfrom() cannot be interrupted politely, so the loop wakes
        # up twice a second to check whether it should still be running.
        sock.settimeout(0.5)
        self._sock = sock
        self.port = sock.getsockname()[1]

    # --------------------------------------------------------- receive
    def _loop(self):
        while self._running:
            sock = self._sock
            if sock is None:
                break
            try:
                data, _addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                # closed under us by close(). Not an error.
                break
            try:
                packet = OscPacket(data)
            except Exception:
                # Malformed packets happen: you are on a UDP port, and
                # VRChat is not the only thing on the network. One log
                # line per bad packet would be a log line per frame.
                continue
            for timed in packet.messages:
                message = timed.message
                if not message.address:
                    continue
                try:
                    self.on_message(message.address, list(message.params))
                except Exception as exc:
                    # A callback that raises must not take the socket
                    # down with it - the plugin would go deaf with no
                    # visible cause.
                    self.api.log(f"OSC: callback raised: {exc}")

    # ------------------------------------------------------------ send
    def send(self, address, *args, to=None):
        """One message out. ``to`` is (ip, port).

        There is no sensible default target here: hardcoding 9000 means
        shouting at a port that may have nothing on it, and the plugin
        cannot tell the difference between that and success. Pass a
        target you actually know.
        """
        if not HAS_OSC or self._sock is None or not to:
            return False
        builder = OscMessageBuilder(address=str(address))
        for arg in args:
            builder.add_arg(arg)
        try:
            self._sock.sendto(builder.build().dgram, (to[0], int(to[1])))
            return True
        except OSError as exc:
            self.api.log(f"OSC: send failed: {exc}")
            return False

    # -------------------------------------------------------- announce
    def advertise(self, paths):
        """Which addresses this plugin accepts, as {address: type tag}.

        "f" float, "i" int, "s" string, "T"/"F" bool. This is what makes
        VRChat push those values here; without it you are send-only in
        practice, no matter what port you are on.

        Replaces the previous set rather than adding to it - otherwise a
        plugin that switched modes keeps being sent the old mode's
        parameters forever.
        """
        self._paths = {str(k): str(v or "f")[:1] or "f"
                       for k, v in (paths or {}).items()
                       if str(k).startswith("/")}
        if self._http is not None:
            self._http.RequestHandlerClass.node_tree = self._tree()

    def _tree(self):
        """The OSCQuery document. Containers get ACCESS 0, the addresses
        you accept get 3 (read+write)."""
        root = {"DESCRIPTION": self.service_name, "FULL_PATH": "/",
                "ACCESS": 0, "CONTENTS": {}}
        for path, tag in self._paths.items():
            node = root
            walked = ""
            for part in [p for p in path.split("/") if p]:
                walked += "/" + part
                node = node.setdefault("CONTENTS", {}).setdefault(
                    part, {"FULL_PATH": walked, "ACCESS": 0})
            node["ACCESS"] = 3
            node["TYPE"] = tag
        return root

    def announce(self):
        """Publish over mDNS so VRChat finds the port by itself.

        SLOW - seconds, because zeroconf probes the network for a name
        conflict before it registers. Do NOT call this from setup() on
        the GUI thread; the app freezes for as long as it takes. Either
        run it on a thread of your own, or accept that the user has to
        type the port somewhere.
        """
        if not HAS_ZEROCONF or self._sock is None:
            if not HAS_ZEROCONF:
                # Not an error. Sending works, and so does receiving
                # from anything told the port by hand. Only automatic
                # discovery is gone - say so once and carry on.
                self.api.log(f"OSC: zeroconf is not installed - "
                             f"udp/{self.port} is not announced")
            return False
        try:
            handler = _handler_class(self._tree(), self.port,
                                     self.service_name)
            self._http = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            self._http_port = self._http.server_address[1]
            threading.Thread(target=self._http.serve_forever,
                             daemon=True).start()

            self._zc = Zeroconf(ip_version=IPVersion.V4Only)
            addr = socket.inet_aton("127.0.0.1")
            name = self.service_name.replace(" ", "-")
            self._infos = [
                ServiceInfo(OSCJSON_TYPE, f"{name}.{OSCJSON_TYPE}",
                            addresses=[addr], port=self._http_port,
                            properties={}),
                ServiceInfo(OSC_TYPE, f"{name}.{OSC_TYPE}",
                            addresses=[addr], port=self.port,
                            properties={}),
            ]
            for info in self._infos:
                self._zc.register_service(info)
            self.api.log(f"OSC: announced as '{name}' (udp/{self.port}, "
                         f"http tcp/{self._http_port})")
            return True
        except Exception as exc:
            self.error = str(exc)
            self.api.log(f"OSC: could not announce ({exc})")
            return False

    # ----------------------------------------------------------- close
    def close(self):
        """Take it all down. Call this from teardown().

        The ORDER matters, and so does the join at the end:

          1. stop announcing, so nothing new discovers us
          2. stop the HTTP server
          3. flip the flag and close the socket, which wakes the loop
          4. WAIT for the thread

        Step 4 is the one people skip. Closing a file descriptor does
        not free the port while a thread is still parked in recvfrom on
        it - the socket stays alive until that call returns. Reopen
        immediately (a settings change, a plugin reload) and you find
        your own old socket in the way, fall back to an automatic port,
        and end up on every port except the one the user just asked for.
        """
        for info in self._infos:
            try:
                self._zc.unregister_service(info)
            except Exception:
                pass
        self._infos = []
        if self._zc is not None:
            try:
                self._zc.close()
            except Exception:
                pass
            self._zc = None
        if self._http is not None:
            try:
                self._http.shutdown()
            except Exception:
                pass
            self._http = None

        self._running = False
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self.port = 0


def _handler_class(node, osc_port, name):
    """A minimal OSCQuery HTTP handler. Answers ?HOST_INFO with the UDP
    port, and everything else with the node tree."""

    class Handler(BaseHTTPRequestHandler):
        node_tree = node
        udp_port = osc_port
        service = name

        def do_GET(self):
            if "HOST_INFO" in (self.path or ""):
                body = {"NAME": type(self).service,
                        "OSC_IP": "127.0.0.1",
                        "OSC_PORT": type(self).udp_port,
                        "OSC_TRANSPORT": "UDP",
                        "EXTENSIONS": {"ACCESS": True, "VALUE": True}}
            else:
                body = type(self).node_tree
            raw = json.dumps(body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *a):
            # the default handler prints every request to stderr
            pass

    return Handler


# ===========================================================================
# HOW MAIN.PY WOULD USE IT
# ===========================================================================
#
# Everything below is commented out on purpose. main.py does not import
# this module, and the example template does not need a port of its own.
#
#     from .osc_advanced_example import OwnOscPort
#
#     _port = None
#     _seen = []          # filled from the receive thread, read by a QTimer
#
#     def setup(api):
#         global _port
#         _port = OwnOscPort(api, _on_osc,
#                            port=api.get("osc_port", 0),
#                            announce=True)
#         if not _port.open():
#             api.set("status", f"OSC: {_port.error}")
#             return
#         # read the port back rather than trusting the wish: a taken
#         # port falls back, and the number you show the user has to be
#         # the one actually in use
#         api.set("status", f"listening on udp/{_port.port}")
#         _port.advertise({"/avatar/parameters/MyThing": "f"})
#
#     def _on_osc(address, args):
#         # RECEIVE THREAD. No Qt here. A list append is fine; the panel
#         # polls it from its own timer.
#         _seen.append((address, args))
#         del _seen[:-20]
#
#     def teardown():
#         global _port
#         if _port is not None:
#             _port.close()
#             _port = None
#         _seen.clear()
#
# Two more things worth knowing if you go this way:
#
#   * A bound socket cannot change its port. If you offer the port as a
#     setting, on_settings() has to close() and open() again - and that
#     is exactly the case the join() in close() exists for.
#
#   * announce() blocks for seconds. Calling it from setup() freezes the
#     whole app while the plugin is switched on. Put it on a thread, or
#     leave announce=False and let the user configure the port at the
#     other end.
