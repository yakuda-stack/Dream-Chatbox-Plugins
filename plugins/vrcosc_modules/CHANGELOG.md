# Changelog

All notable changes to this plugin are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-08-05

### Added
- Initial release: eleven blocks of Bluscream's
  [VRCOSC-Modules](https://github.com/Bluscream/VRCOSC-Modules) (GPL-3.0)
  ported to Linux and to the OSC-DreamChatbox plugin system.
- Every block is its own collapsible group with an `enable` switch. A block
  that is off starts no thread, no socket and no subprocess, and its
  placeholders stay empty.
- **Linux Hardware Stats** - CPU/GPU load, power, temperatures, RAM, VRAM,
  network, MangoHud FPS, active window, VR mode, VRChat detection.
- **Linux Media** - title, artist, player, position and a progress bar from
  any MPRIS player.
- **Linux Process Manager** - watch list matched against process names and
  full command lines, so Proton games are found too.
- **OpenXR** - active runtime, VR session detection (WiVRn only counts with a
  connected headset) and VR uptime.
- **Home Assistant** - three entity slots over the REST API, with units,
  rounding and prettified text states.
- **HTTP** - timed request against any URL with a dotted JSON path.
- **HTTP / MCP Server** - `/status`, `/text`, `/mcp` with `tools/list` and
  `tools/call`; refuses to bind to the network without a token.
- **IRC Bridge** - read-only client with TLS, PING/PONG and reconnect backoff.
- **VRCX Bridge** - world, friends online and friend events from the VRCX
  SQLite database, opened read-only and looked up by table suffix.
- **VRChat Settings** - two dotted keys out of VRChat's `config.json`.
- **Notifications** - fires on a change in the other blocks and sends to
  `notify-send`, XSOverlay (UDP 42010) or a webhook, on a worker thread with
  a bounded queue.
- 77 global placeholders and a combined `{vrcosc_modules}` line with a
  configurable separator.
- `vrcosc_hwstats.sh` and `vrcosc_mpris_query.sh` bundled unchanged; patched
  copies are deployed to the cache directory so a parallel VRCOSC install is
  not disturbed.
- `configs/config.json` generated from the manifest defaults, a GPL-3.0
  `LICENSE` and a `THIRD_PARTY_NOTICES.md` mapping every block to its origin.

### Not included
- `OpenXRGestureExtensions` and `OpenXRHapticControl` - both need to join the
  running XR session, which a chatbox side process cannot do.
- Starting or killing processes, and writing VRChat's config.

[1.0.0]: https://github.com/yakuda-stack/Dream-Chatbox-Plugins
