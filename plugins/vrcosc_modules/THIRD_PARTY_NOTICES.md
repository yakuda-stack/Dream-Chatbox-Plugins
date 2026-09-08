# Third Party Notices

This plugin is a derivative work of Bluscream's VRCOSC module set. Every
block below started life as one of his modules.

- **Author:** Bluscream
- **Project:** https://github.com/Bluscream/VRCOSC-Modules
- **License:** GPL-3.0

---

## Bundled verbatim

| File | Origin | Change |
|---|---|---|
| `vrcosc_hwstats.sh` | `VRCOSC.Modules/LinuxHardwareStats/vrcosc_hwstats.sh` | none |
| `vrcosc_mpris_query.sh` | `VRCOSC.Modules/LinuxMedia/vrcosc_mpris_query.sh` | none |

Both scripts are byte-identical to upstream. At runtime a patched copy is
written to `~/.cache/osc-dreamchatbox/vrcosc_modules/`. The patches are
minimal and mechanical:

- `vrcosc_hwstats.sh` — the three settings assignments at the top
  (`GPU_INDEX`, `CPU_INDEX`, `NET_IFACE`), exactly as VRCOSC rewrites them
  at deploy time, plus the output path in the final heredoc.
- `vrcosc_mpris_query.sh` — the output path, and the debug log the script
  appends to on every call (at a three second interval that file would
  grow without end).

The output paths are moved so a real VRCOSC installation on the same
machine keeps its own `~/.vrcosc_hwstats.txt` and `~/.vrcosc_mpris.txt`.

## Rewritten in Python

| Block | Upstream module | Note |
|---|---|---|
| Linux Hardware Stats | `LinuxHardwareStats` | wrapper around his script |
| Linux Media | `LinuxMedia` | wrapper around his script |
| Linux Process Manager | `LinuxProcessManager` | watch half only; no start/kill |
| OpenXR | `OpenXRStatistics` | runtime/session/uptime; the in-session statistics need an XR session |
| Home Assistant | `HomeAssistant` | REST reading; no WebSocket, no Jinja templates, no OSC parameters |
| HTTP | `HTTP` | timed request instead of flow nodes |
| HTTP / MCP Server | `HTTPServer` | four routes plus an MCP tool endpoint |
| IRC Bridge | `IRCBridge` | read only |
| VRCX Bridge | `VRCXBridge` | SQLite instead of the Windows named pipe |
| VRChat Settings | `VRChatSettings` | read only |
| Notifications | `Notifications` | desktop, XSOverlay and webhook targets; OVRToolkit needs a WebSocket library the chatbox does not ship |

Not ported: `OpenXRGestureExtensions` and `OpenXRHapticControl` (need to
join the running XR session), `Debug` (needs raw OSC parameter access),
`DesktopFPS` (covered by the hardware block on Linux).

The settings layout, the chatbox variables and the detection logic of the
blocks follow his modules; the READMEs in his repository were the
specification.

---

## License

Both the upstream modules and this plugin are GPL-3.0. The full text is in
[`LICENSE`](LICENSE).

    Copyright (c) Bluscream        - vrcosc_hwstats.sh, vrcosc_mpris_query.sh,
                                     and the design of every block
    Copyright (C) 2026 yakuda      - the Python port, packaging and docs

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.
