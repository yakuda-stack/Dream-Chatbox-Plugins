# VRCOSC Modules (Linux) — plugin for OSC-DreamChatbox

Bluscream's [VRCOSC-Modules](https://github.com/Bluscream/VRCOSC-Modules)
ported to Linux and to the OSC-DreamChatbox plugin system. Eleven blocks
in one plugin, each collapsible and each with its own **Enable** switch.

**A block that is off costs nothing.** No thread, no socket, no
subprocess, no request — and its placeholders stay empty, so they drop out
of the line together with their separators.

---

## The blocks

| Block | What it gives you | Needs |
|---|---|---|
| **Linux Hardware Stats** | CPU/GPU load, Watt, temps, RAM, VRAM, network, MangoHud FPS, active window, VR mode, VRChat running | `bash`; `nvidia-smi` on NVIDIA; optional MangoHud, xdotool/kdotool |
| **Linux Media** | title, artist, player, position, progress bar from any MPRIS player | `dbus-send` |
| **Linux Process Manager** | which of your watched programs are running | — |
| **OpenXR** | runtime, VR session, VR uptime | — |
| **Home Assistant** | three entity states | URL + long-lived token |
| **HTTP** | any URL or JSON API as text | — |
| **HTTP / MCP Server** | REST + MCP endpoint into the chatbox | — |
| **IRC Bridge** | newest message from a channel | — |
| **VRCX Bridge** | world, friends online, friend events | VRCX installed |
| **VRChat Settings** | two values out of VRChat's `config.json` | — |
| **Notifications** | pushes changes to desktop, XSOverlay or a webhook | `notify-send` for the desktop target |

Everything is stdlib Python plus two shell scripts. The plugin installs
nothing and pulls in no packages.

## What could not be ported

**OpenXR Gesture Extensions** and **OpenXR Haptic Control** are not in
here. Both load `openxr_loader` and join the running XR session as an
application: hand tracking and controller haptics belong to the headset
app, and a chatbox side process has no session to join. The **OpenXR**
block covers what *is* readable from outside — which runtime is selected,
whether a compositor is up, whether a headset is really connected, and
for how long.

**VRCX Bridge** took a different road than upstream. The Windows version
talks over a named pipe (`\\.\pipe\vrcx-ipc`), which does not exist here,
so this one reads VRCX's SQLite database instead — read-only, and by table
suffix rather than by name, because the schema belongs to VRCX.

Two upstream features were left out on purpose, not for lack of a way:
**Process Manager** cannot start or kill anything, and **VRChat Settings**
cannot write. A chatbox plugin that kills programs or edits the config of
a running game is a footgun.

## Install

Drop the `vrcosc_modules` folder into the plugin directory of
OSC-DreamChatbox, or install the ZIP from the Plugins page. Open the
Settings, expand a block, switch it on.

The plugin never writes into its own folder. The two bundled scripts are
copied to `~/.cache/osc-dreamchatbox/vrcosc_modules/` and patched there —
including their output paths, so a real VRCOSC installation on the same
machine keeps its own `~/.vrcosc_hwstats.txt` and `~/.vrcosc_mpris.txt`.

## Placeholders

77 of them, one prefix per block: `hw_` hardware, `md_` media, `pm_`
processes, `xr_` OpenXR, `ha_` Home Assistant, `ht_` HTTP, `sv_` server,
`irc_` IRC, `vx_` VRCX, `vc_` VRChat config, `nt_` notifications. They are
registered globally — use them in status texts, in the Apps custom strings
and in All-in-one.

Every block also has one combined value meant for a line:
`{hw_cpu}` `{hw_gpu}` `{hw_ram}` `{md_media}` `{pm_list}` `{xr_vr}`
`{ha_all}` `{ht_text}` `{sv_text}` `{irc_msg}` `{vx_friends}` `{vc_all}`.
`{vrcosc_modules}` joins whatever is enabled, in block order, with the
separator from the General block.

## The HTTP / MCP server

Off by default. When on, it binds to `127.0.0.1` on port 8723:

```
GET  /                          plain text help
GET  /status                    every filled placeholder as JSON
GET  /text?msg=hello            sets {sv_text}
POST /text                      same, body is the text or {"text": "…"}
GET  /mcp                       tool discovery
POST /mcp                       JSON-RPC: tools/list, tools/call
```

Two MCP tools: `set_chatbox_text` and `get_status`. Handy for a Stream
Deck, a shell script, or letting an assistant write a line for you.

Binding to the network needs a token — the plugin refuses to open the port
otherwise. Anything that can reach it can write into your chatbox.

## Notifications

The Notifications block watches values the other blocks produce and fires
when one *changes*: a new IRC message, a friend event from VRCX, a track
change, a different HTTP answer, a Home Assistant state. The first value
after a start is a state, not an event, so switching a block on does not
spam you. Sending happens on a worker thread with a bounded queue — a slow
webhook drops a notification rather than stalling the chatbox.

## Notes

- WiVRn idles with its server process running, so it only counts as an
  active session once its compositor socket has a connected client.
- Values that go stale (a hung script, a dead network) are dropped rather
  than shown — an empty slot beats a number from ten minutes ago.
- Tokens and passwords live in the plugin's `config.json` in plain text.
  Use credentials of your own, never somebody else's.

## License

GPL-3.0-or-later, inherited from the upstream modules.

- `vrcosc_hwstats.sh`, `vrcosc_mpris_query.sh` — Copyright (c) Bluscream
- everything else — Copyright (C) 2026 yakuda

See [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) for the full
attribution, block by block.
