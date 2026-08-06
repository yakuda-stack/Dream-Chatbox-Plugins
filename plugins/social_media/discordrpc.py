"""Talks to the Discord desktop app over its local RPC socket.

Discord ships a small IPC endpoint next to the client – a unix socket on
Linux, a named pipe on Windows – that games use for Rich Presence. The
same channel can be asked which voice channel the user is currently
sitting in, which is exactly what we want for the chatbox.

Protocol in three lines:

    frame  = uint32 opcode (LE) + uint32 length (LE) + JSON payload
    opcode = 0 handshake, 1 frame, 2 close, 3 ping, 4 pong
    every command carries a nonce, the answer echoes it back

Getting past the handshake needs an OAuth token, and the ``rpc`` scope
is whitelist-only – *except* for the owner of the application. That is
why the user registers an app of their own instead of shipping one with
the plugin: no bot, no server, no shared secret, and the token belongs
to the person who made it.

Nothing in here touches Qt or the chatbox. It is called from the worker
thread only.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import os
import socket
import struct
import time
import uuid

from .netutil import HttpError, request_json

OP_HANDSHAKE = 0
OP_FRAME = 1
OP_CLOSE = 2
OP_PING = 3
OP_PONG = 4

# rpc.voice.read is the one that matters; identify/guilds are plain
# scopes and give us the guild names over HTTP if the RPC call for them
# is refused.
SCOPES = ["rpc", "rpc.voice.read", "identify", "guilds"]

TOKEN_URL = "https://discord.com/api/oauth2/token"
GUILDS_URL = "https://discord.com/api/v10/users/@me/guilds"

CONNECT_TIMEOUT = 2.0
COMMAND_TIMEOUT = 5.0
# the AUTHORIZE popup waits for a human to click "Authorize"
AUTHORIZE_TIMEOUT = 120.0
GUILD_CACHE_SECS = 600.0
MAX_FRAME = 1 << 20


def empty_state():
    return {"connected": False, "guild": None, "channel": None, "error": ""}


# ------------------------------------------------------------ socket
def _runtime_dirs():
    """Every folder a Discord socket has ever been found in."""
    bases = []
    for env in ("XDG_RUNTIME_DIR", "TMPDIR", "TMP", "TEMP"):
        value = os.environ.get(env)
        if value:
            bases.append(value)
    bases.append("/tmp")

    dirs = []
    for base in bases:
        # native client
        dirs.append(base)
        # flatpak / snap sandboxes put theirs one level down
        for extra in (
            os.path.join("app", "com.discordapp.Discord"),
            os.path.join("app", "com.discordapp.DiscordCanary"),
            os.path.join("app", "com.discordapp.DiscordPTB"),
            os.path.join("app", "dev.vencord.Vesktop"),
            os.path.join("app", "io.github.equicord.equibop"),
            "snap.discord",
            os.path.join(".flatpak", "dev.vencord.Vesktop", "xdg-run"),
        ):
            dirs.append(os.path.join(base, extra))

    seen, out = set(), []
    for folder in dirs:
        if folder not in seen:
            seen.add(folder)
            out.append(folder)
    return out


def candidate_paths():
    if os.name == "nt":
        return [r"\\?\pipe\discord-ipc-%d" % i for i in range(10)]
    paths = []
    for folder in _runtime_dirs():
        for i in range(10):
            paths.append(os.path.join(folder, f"discord-ipc-{i}"))
    return paths


class _Pipe:
    """One connection, unix socket or named pipe, same two methods."""

    def __init__(self, path, timeout=CONNECT_TIMEOUT):
        self._sock = None
        self._file = None
        if os.name == "nt":
            self._file = open(path, "r+b", buffering=0)
        else:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect(path)
            self._sock = sock

    def settimeout(self, seconds):
        if self._sock is not None:
            self._sock.settimeout(seconds)

    def write(self, data):
        if self._sock is not None:
            self._sock.sendall(data)
        else:
            self._file.write(data)
            self._file.flush()

    def read(self, size):
        chunks = bytearray()
        while len(chunks) < size:
            want = size - len(chunks)
            part = (self._sock.recv(want) if self._sock is not None
                    else self._file.read(want))
            if not part:
                raise OSError("Discord closed the connection")
            chunks += part
        return bytes(chunks)

    def close(self):
        for handle in (self._sock, self._file):
            try:
                if handle is not None:
                    handle.close()
            except Exception:
                pass
        self._sock = self._file = None


# --------------------------------------------------------------- client
class DiscordRPC:
    """Connect, log in once, then answer 'where am I' on every poll."""

    def __init__(self, log, store_path=""):
        self.log = log
        self._store = store_path
        self._pipe = None
        self._client_id = ""
        self._authed = False
        self._guilds = {}
        self._guilds_at = 0.0
        self._token = None
        self._next_try = 0.0
        self._authorize_after = 0.0
        self._fails = 0
        self._last = empty_state()
        self._last_error = ""

    # ------------------------------------------------------------ public
    def set_store(self, path):
        self._store = path

    def reset(self):
        """Credentials changed – drop the connection and the cached
        token, the next poll starts from scratch."""
        self._drop()
        self._token = None
        self._guilds = {}
        self._guilds_at = 0.0
        self._next_try = 0.0
        self._authorize_after = 0.0
        self._fails = 0
        self._last = empty_state()

    def close(self):
        self._drop()

    def poll(self, conf):
        """conf: client_id, client_secret, redirect. Never raises."""
        now = time.time()
        if now < self._next_try:
            return dict(self._last)
        if not conf.get("client_id"):
            self._last = empty_state()
            self._last["error"] = "no Application-ID"
            return dict(self._last)

        try:
            self._ensure(conf)
            channel = self._command("GET_SELECTED_VOICE_CHANNEL", {})
            state = empty_state()
            state["connected"] = True
            if channel:
                state["channel"] = (channel.get("name") or "").strip() or None
                guild_id = channel.get("guild_id")
                if guild_id:
                    state["guild"] = self._guild_name(str(guild_id))
            self._last = state
            self._fails = 0
            self._last_error = ""
        except Exception as e:
            self._drop()
            self._fails = min(self._fails + 1, 6)
            # 5s, 10s … 30s – a closed Discord must not spin the thread
            self._next_try = now + 5 * self._fails
            self._last = empty_state()
            self._last["error"] = str(e)
            if str(e) != self._last_error:
                self._last_error = str(e)
                self.log(f"discord: {e}")
        return dict(self._last)

    # ----------------------------------------------------------- connect
    def _drop(self):
        if self._pipe is not None:
            try:
                self._pipe.close()
            except Exception:
                pass
        self._pipe = None
        self._authed = False

    def _ensure(self, conf):
        client_id = str(conf["client_id"]).strip()
        if self._pipe is not None and self._authed \
                and client_id == self._client_id:
            return
        if client_id != self._client_id:
            self._drop()
            self._token = None
            self._client_id = client_id
        if self._pipe is None:
            self._connect(client_id)
        if not self._authed:
            self._auth(conf)

    def _connect(self, client_id):
        last = None
        reached = False
        for path in candidate_paths():
            if os.name != "nt" and not os.path.exists(path):
                continue
            try:
                pipe = _Pipe(path)
            except Exception as e:
                last = e
                continue
            self._pipe = pipe
            reached = True
            try:
                self._handshake(client_id)
                return
            except Exception as e:
                last = e
                self._drop()
        if reached:
            # a socket was there but would not talk to us – almost always
            # a wrong Application-ID
            raise RuntimeError(f"Discord did not accept the connection "
                               f"({last})")
        raise RuntimeError("no running Discord found")

    def _handshake(self, client_id):
        self._pipe.settimeout(COMMAND_TIMEOUT)
        self._send(OP_HANDSHAKE, {"v": 1, "client_id": client_id})
        deadline = time.time() + COMMAND_TIMEOUT
        while time.time() < deadline:
            op, payload = self._recv()
            if op == OP_CLOSE:
                raise RuntimeError(payload.get("message")
                                   or "handshake refused – wrong "
                                      "Application-ID?")
            if op == OP_PING:
                self._send(OP_PONG, payload)
                continue
            if payload.get("evt") == "READY":
                return
        raise RuntimeError("Discord did not answer the handshake")

    # -------------------------------------------------------------- auth
    def _auth(self, conf):
        token = self._token or self._load_token()
        if token and self._try_authenticate(token.get("access_token")):
            self._token = token
            return

        secret = str(conf.get("client_secret") or "").strip()
        if token and token.get("refresh_token") and secret:
            fresh = self._refresh(conf, token["refresh_token"])
            if fresh and self._try_authenticate(fresh.get("access_token")):
                self._token = fresh
                self._save_token(fresh)
                return

        if not secret:
            raise RuntimeError("Client-Secret missing – needed once to log in")

        now = time.time()
        if now < self._authorize_after:
            raise RuntimeError("waiting before asking for permission again")
        # one popup per minute at most, whatever goes wrong
        self._authorize_after = now + 60

        code = self._authorize(conf)
        fresh = self._exchange(conf, code)
        if not self._try_authenticate(fresh.get("access_token")):
            raise RuntimeError("login was accepted but authenticate failed")
        self._token = fresh
        self._save_token(fresh)
        self.log("logged in – Discord will not ask again")

    def _try_authenticate(self, access_token):
        if not access_token:
            return False
        try:
            self._command("AUTHENTICATE", {"access_token": access_token})
        except Exception:
            return False
        self._authed = True
        return True

    def _authorize(self, conf):
        """Pops the 'Authorize' dialog inside the Discord client."""
        self.log("asking Discord for permission – click Authorize "
                 "in the popup")
        data = self._command("AUTHORIZE",
                             {"client_id": self._client_id,
                              "scopes": SCOPES},
                             timeout=AUTHORIZE_TIMEOUT)
        code = (data or {}).get("code")
        if not code:
            raise RuntimeError("no authorization code came back")
        return code

    def _exchange(self, conf, code):
        try:
            data = request_json(TOKEN_URL, data={
                "client_id": self._client_id,
                "client_secret": str(conf.get("client_secret") or "").strip(),
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": str(conf.get("redirect")
                                    or "http://localhost").strip(),
            })
        except HttpError as e:
            raise RuntimeError(f"token exchange refused – check the "
                               f"Client-Secret and the Redirect-URI ({e})")
        return self._stamp(data)

    def _refresh(self, conf, refresh_token):
        try:
            data = request_json(TOKEN_URL, data={
                "client_id": self._client_id,
                "client_secret": str(conf.get("client_secret") or "").strip(),
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            })
        except Exception:
            return None
        return self._stamp(data)

    @staticmethod
    def _stamp(data):
        token = dict(data or {})
        try:
            token["expires_at"] = time.time() + float(token.get("expires_in", 0))
        except Exception:
            token["expires_at"] = 0.0
        return token

    # ------------------------------------------------------------- store
    def _load_token(self):
        if not self._store or not os.path.exists(self._store):
            return None
        try:
            with open(self._store, "r", encoding="utf-8") as fh:
                blob = json.load(fh)
        except Exception:
            return None
        token = blob.get(self._client_id)
        return token if isinstance(token, dict) else None

    def _save_token(self, token):
        if not self._store:
            return
        blob = {}
        if os.path.exists(self._store):
            try:
                with open(self._store, "r", encoding="utf-8") as fh:
                    blob = json.load(fh) or {}
            except Exception:
                blob = {}
        blob[self._client_id] = token
        try:
            folder = os.path.dirname(self._store)
            if folder:
                os.makedirs(folder, exist_ok=True)
            tmp = self._store + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(blob, fh, indent=2)
            os.replace(tmp, self._store)
            os.chmod(self._store, 0o600)
        except Exception as e:
            self.log(f"could not save the token: {e}")

    # ------------------------------------------------------------ guilds
    def _guild_name(self, guild_id):
        name = self._guilds.get(guild_id)
        if name and time.time() - self._guilds_at < GUILD_CACHE_SECS:
            return name
        if time.time() - self._guilds_at >= GUILD_CACHE_SECS or not name:
            self._load_guilds()
        return self._guilds.get(guild_id)

    def _load_guilds(self):
        self._guilds_at = time.time()
        names = {}
        try:
            data = self._command("GET_GUILDS", {})
            for guild in (data or {}).get("guilds") or []:
                if guild.get("id") and guild.get("name"):
                    names[str(guild["id"])] = guild["name"]
        except Exception:
            names = {}
        if not names:
            # GET_GUILDS can be refused depending on the granted scopes –
            # the plain HTTP list works with the 'guilds' scope alone
            names = self._http_guilds()
        if names:
            self._guilds = names

    def _http_guilds(self):
        token = (self._token or {}).get("access_token")
        if not token:
            return {}
        try:
            data = request_json(
                GUILDS_URL,
                headers={"Authorization": f"Bearer {token}"})
        except Exception as e:
            self.log(f"could not read the server list: {e}")
            return {}
        names = {}
        for guild in data or []:
            if guild.get("id") and guild.get("name"):
                names[str(guild["id"])] = guild["name"]
        return names

    # ------------------------------------------------------------ frames
    def _send(self, opcode, payload):
        blob = json.dumps(payload).encode("utf-8")
        self._pipe.write(struct.pack("<II", opcode, len(blob)) + blob)

    def _recv(self):
        header = self._pipe.read(8)
        opcode, length = struct.unpack("<II", header)
        if length > MAX_FRAME:
            raise RuntimeError("frame too large – not a Discord socket")
        body = self._pipe.read(length) if length else b"{}"
        try:
            return opcode, json.loads(body.decode("utf-8", "replace"))
        except Exception:
            return opcode, {}

    def _command(self, cmd, args, timeout=COMMAND_TIMEOUT):
        """Send one command and wait for the answer with our nonce.

        Events (VOICE_CHANNEL_SELECT and friends) can arrive in between;
        they carry no nonce and are simply skipped.
        """
        if self._pipe is None:
            raise RuntimeError("not connected")
        nonce = uuid.uuid4().hex
        self._pipe.settimeout(timeout)
        self._send(OP_FRAME, {"cmd": cmd, "args": args, "nonce": nonce})
        deadline = time.time() + timeout
        while time.time() < deadline:
            opcode, payload = self._recv()
            if opcode == OP_CLOSE:
                raise RuntimeError(payload.get("message") or "connection closed")
            if opcode == OP_PING:
                self._send(OP_PONG, payload)
                continue
            if payload.get("nonce") != nonce:
                continue
            if payload.get("evt") == "ERROR":
                data = payload.get("data") or {}
                raise RuntimeError(data.get("message")
                                   or f"{cmd} was refused")
            return payload.get("data")
        raise RuntimeError(f"{cmd} timed out")
