# Linux Extras (from VRCOSC) — plugin for OSC-DreamChatbox

Bluscream's [VRCOSC-Modules](https://github.com/Bluscream/VRCOSC-Modules)
ported to Linux and to the OSC-DreamChatbox plugin system. Plugin id:
`vrcosc_modules`.

**Since v2.0.0 it only does what the app and the other plugins don't.**
CPU, GPU, temperatures and the song title are in the app itself, FPS and
battery in World Stats – so those were taken out here.

**A block that is off costs nothing.** No thread, no socket, no
subprocess, no request — and its placeholders stay empty, so they drop out
of the line together with their separators.

---

## The blocks

| Block | What it gives you | Needs |
|---|---|---|
| 🖥️ **System** | RAM and VRAM in GB, network speed / totals / peaks / utilisation, active window, VR mode, VRChat running | `bash`; `nvidia-smi` on NVIDIA; optional xdotool/kdotool |
| 🎵 **Player status** | ▶/⏸ icon and volume from any MPRIS player | `dbus-send` |
| 📋 **Programs** | which of your watched programs are running | — |
| 🥽 **OpenXR** | runtime, VR session, VR uptime | — |
| 👥 **VRCX** | world, friends online, friend events | VRCX installed |
| ⚙️ **VRChat settings** | two values out of VRChat's `config.json` | — |
| 🏠 **Home Assistant** | three entity states | URL + long-lived token |
| 🌐 **HTTP** | any URL or JSON API as text | — |
| 💬 **IRC** | newest message from a channel | — |
| 🔌 **Server** | REST + MCP endpoint into the chatbox | — |
| 🔔 **Notifications** | pushes changes to desktop, XSOverlay or a webhook | `notify-send` for the desktop target |

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

62 of them, one prefix per block: `hw_` system, `md_` player status, `pm_`
programs, `xr_` OpenXR, `vx_` VRCX, `vc_` VRChat settings, `ha_` Home
Assistant, `ht_` HTTP, `irc_` IRC, `sv_` server, `nt_` notifications –
plus the network extras `net_max_down` `net_max_up` `net_total_down`
`net_total_up` `net_utilization` (names from the chatbox converter). All
are registered globally — use them in status texts, in the Apps custom
strings and in All-in-one.

Handy combined values: `{hw_ram}` `{hw_vram}` `{hw_net}` `{md_status}`
`{pm_list}` `{xr_vr}` `{vx_friends}` `{ha_all}` `{ht_text}` `{irc_msg}`
`{vc_all}`. `{vrcosc_modules}` joins whatever is enabled, in block order,
with the separator from the General block.

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
when one *changes*: a new IRC message, a friend event from VRCX, a
different HTTP answer, a Home Assistant state. The first value
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
