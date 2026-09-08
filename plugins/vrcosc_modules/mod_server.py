"""HTTP Server - a small REST + MCP endpoint for the chatbox.

Port of `HTTPServer` from Bluscream's VRCOSC-Modules. His version serves
a full OpenAPI surface plus an MCP web module; this one keeps the four
routes that are useful from outside:

    GET  /              plain text help
    GET  /status        every placeholder of the suite as JSON
    GET  /text?msg=…    set {sv_text}, e.g. from a Stream Deck or a script
    POST /text          same, body is the text (or {"text": "…"})
    GET  /mcp           MCP-style tool discovery
    POST /mcp           JSON-RPC: tools/list and tools/call

It binds to 127.0.0.1 by default. Opening it to the LAN is one setting
away, and then the token is not optional - anything that can reach the
port can write into your chatbox.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import util

ID = "sv"
NAME = "HTTP / MCP Server"

KEYS = ("sv_text", "sv_requests", "sv_status", "sv_port")

MAX_BODY = 64 * 1024

_server = None
_thread = None
_state = {"text": "", "requests": 0, "port": 0}
_lock = threading.Lock()
_provider = None          # set by main.py: callable -> dict of all values
_token = ""

TOOLS = [
    {"name": "set_chatbox_text",
     "description": "Set the {sv_text} placeholder of OSC-DreamChatbox.",
     "inputSchema": {"type": "object", "properties": {
         "text": {"type": "string"}}, "required": ["text"]}},
    {"name": "get_status",
     "description": "Read every placeholder the plugin suite currently fills.",
     "inputSchema": {"type": "object", "properties": {}}},
]

HELP = """OSC-DreamChatbox - VRCOSC Modules suite

GET  /status        all placeholders as JSON
GET  /text?msg=hi   set {sv_text}
POST /text          same, text or {"text": "..."} in the body
GET  /mcp           tool discovery
POST /mcp           JSON-RPC: tools/list, tools/call

Add ?token=... or an Authorization header when a token is configured.
"""


def set_provider(fn):
    """main.py hands us a way to read the current values of every module."""
    global _provider
    _provider = fn


# ---------------------------------------------------------------- setup
def start(ctx):
    global _server, _thread, _token
    stop()
    port = ctx.num("sv_port", 8723, 1024, 65535)
    host = "0.0.0.0" if ctx.get("sv_bind", "local") == "all" else "127.0.0.1"
    _token = ctx.text("sv_token")
    if host == "0.0.0.0" and not _token:
        ctx.log("server: LAN binding without a token - refusing to start")
        return
    try:
        _server = ThreadingHTTPServer((host, port), _Handler)
    except OSError as e:
        ctx.log(f"server: cannot bind {host}:{port} ({e})")
        _server = None
        return
    _server.daemon_threads = True
    with _lock:
        _state["port"] = port
        _state["requests"] = 0
    _thread = threading.Thread(target=_server.serve_forever,
                               kwargs={"poll_interval": 0.5},
                               name="httpserver", daemon=True)
    _thread.start()
    ctx.log(f"server: listening on http://{host}:{port}")


def stop():
    global _server, _thread
    if _server is not None:
        try:
            _server.shutdown()
            _server.server_close()
        except Exception:
            pass
    _server = None
    if _thread is not None:
        _thread.join(timeout=3.0)
    _thread = None
    with _lock:
        _state["port"] = 0


def on_settings(ctx):
    """Port, binding and token only take effect on a restart of the
    socket, so just do that - it takes a few milliseconds."""
    if _server is None:
        return
    changed = (ctx.num("sv_port", 8723, 1024, 65535) != _state["port"]
               or ctx.text("sv_token") != _token)
    if changed:
        start(ctx)


def values(ctx):
    vals = dict.fromkeys(KEYS)
    if _server is None:
        return vals
    with _lock:
        text, requests, port = _state["text"], _state["requests"], _state["port"]
    vals["sv_port"] = str(port)
    vals["sv_requests"] = str(requests)
    vals["sv_status"] = util.join(ctx.text("sv_icon", "🌐"), str(port))
    if text:
        vals["sv_text"] = util.cut(text, ctx.num("sv_max", 60, 4, 140)) or None
    return vals


def line(vals):
    return [vals["sv_text"]]


# -------------------------------------------------------------- handler
class _Handler(BaseHTTPRequestHandler):
    server_version = "DreamChatbox"
    protocol_version = "HTTP/1.1"

    # the default handler prints every request to stderr
    def log_message(self, *_args):
        pass

    # -- helpers -----------------------------------------------------
    def _count(self):
        with _lock:
            _state["requests"] += 1

    def _authorised(self, query):
        if not _token:
            return True
        header = self.headers.get("Authorization", "")
        given = header.split(" ", 1)[-1] if header else ""
        return _token in (given, (query.get("token") or [""])[0])

    def _send(self, status, body, kind="application/json"):
        if not isinstance(body, (bytes, bytearray)):
            body = str(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{kind}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _json(self, status, payload):
        self._send(status, json.dumps(payload, ensure_ascii=False))

    def _body(self):
        length = util.to_int(self.headers.get("Content-Length", 0))
        return self.rfile.read(min(length, MAX_BODY)).decode(
            "utf-8", errors="replace") if length > 0 else ""

    def _snapshot(self):
        try:
            return _provider() if _provider else {}
        except Exception:
            return {}

    def _set_text(self, text):
        text = " ".join(str(text or "").split())[:500]
        with _lock:
            _state["text"] = text
        return text

    # -- routes ------------------------------------------------------
    def do_GET(self):
        self._count()
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        route = parsed.path.rstrip("/") or "/"

        if route == "/":
            return self._send(200, HELP, "text/plain")
        if not self._authorised(query):
            return self._json(401, {"error": "token required"})
        if route == "/status":
            return self._json(200, {"values": self._snapshot()})
        if route == "/text":
            text = (query.get("msg") or query.get("text") or [""])[0]
            return self._json(200, {"text": self._set_text(text)})
        if route == "/mcp":
            return self._json(200, {"tools": TOOLS})
        return self._json(404, {"error": "unknown route"})

    def do_POST(self):
        self._count()
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        route = parsed.path.rstrip("/") or "/"
        raw = self._body()
        if not self._authorised(query):
            return self._json(401, {"error": "token required"})

        if route == "/text":
            text = raw
            try:                            # a JSON body is fine too
                text = json.loads(raw).get("text", raw)
            except ValueError:
                pass
            return self._json(200, {"text": self._set_text(text)})

        if route == "/mcp":
            return self._mcp(raw)
        return self._json(404, {"error": "unknown route"})

    def _mcp(self, raw):
        """Just enough JSON-RPC for tools/list and tools/call."""
        try:
            call = json.loads(raw or "{}")
        except ValueError:
            return self._json(400, {"error": "bad json"})
        rpc = call.get("id")
        method = call.get("method", "")
        params = call.get("params") or {}

        if method == "tools/list":
            return self._json(200, {"jsonrpc": "2.0", "id": rpc,
                                    "result": {"tools": TOOLS}})
        if method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            if name == "set_chatbox_text":
                text = self._set_text(args.get("text", ""))
                result = {"content": [{"type": "text", "text": text}]}
            elif name == "get_status":
                result = {"content": [{"type": "text", "text": json.dumps(
                    self._snapshot(), ensure_ascii=False)}]}
            else:
                return self._json(200, {"jsonrpc": "2.0", "id": rpc,
                                        "error": {"code": -32601,
                                                  "message": "unknown tool"}})
            return self._json(200, {"jsonrpc": "2.0", "id": rpc,
                                    "result": result})
        return self._json(200, {"jsonrpc": "2.0", "id": rpc,
                                "error": {"code": -32601,
                                          "message": "unknown method"}})
