# OSCLeash plugin for OSC-DreamChatbox

Runs [OSCLeash](https://github.com/ZenithVal/OSCLeash) by ZenithVal as a
child process of the chatbox – one process per leash, each with its own
generated `Config.json`, its own Start/Stop button and its own console.

OSCLeash itself is **not** bundled and **not** modified. It stays MIT
licensed and is installed separately (AUR `OSCLeash`, the AppImage from
the releases page, or a source checkout).

## The panel

* **Start all / Stop all** – everything at once.
* **✚ Add leash** – one more instance. Needed whenever a second leash has
  its own compass: OSCLeash's `DirectionalParameters` are a single set
  per config, so a tail with its own contacts cannot share the config of
  the collar.
* **▶ / ■** per row – start or stop exactly this instance.
* **🐞 Debug** – the raw output of exactly this process, in its own
  window. Colour codes are stripped, repeated lines are collapsed into
  `(xN)`, and the plugin's own notes are marked with `»`.
* **⚙** – the settings of this leash: physbone parameters, contact
  prefix, ports, deadzones, strength, turning, delays.
* **🗑** – remove the leash and its generated config.

Instance settings are read when the instance **starts**. Changing
something while it runs shows a reminder to stop and start it again.

## Ports and OSCQuery

VRChat sends avatar data to port 9001 exactly once. A second instance on
the same machine therefore only receives anything with **OSCQuery**
switched on, which is why every leash added after the first gets it
enabled automatically. The panel warns when two running instances
without OSCQuery listen on the same port.

## Placeholders

| Placeholder | Meaning |
| --- | --- |
| `{oscleash}` | ready-made line, e.g. `🐕 Leash ↗` |
| `{oscleash_state}` | `pulled`, or `ready` while idle |
| `{oscleash_dir}` | ↑ ↗ → ↘ ↓ ↙ ← ↖ |
| `{oscleash_compass}` | `N` `NE` `E` … |
| `{oscleash_name}` | the leash currently being pulled |
| `{oscleash_count}` | running instances |

Direction and strength are read from OSCLeash's log output, so the
instance needs **Debug output** switched on (it is, by default). Without
it the plugin still knows *that* a leash is being pulled, just not where
to. Nothing here opens a second OSC port – a second listener would take
data away from OSCLeash itself.

## Where things are stored

```
plugins/oscleash/configs/instances.json          the leash list
plugins/oscleash/configs/instances/<id>/Config.json   generated per start
```

`OSCLEASH_CONFIG_PATH` points each process at its own file, so the
plugin never touches `~/.config/OSCLeash/Config.json`.

## OSCLeash is included

There is nothing to install. `vendor/OSCLeash/` inside this plugin holds
OSCLeash itself, plus `python-osc` and `tinyoscquery`, so the Start
button runs a script that is already on disk – no AUR package, no
AppImage, no `chmod`, no pip, and the same story on Windows and Linux.
See `vendor/VENDOR.md` for versions, licences and the one modification.

Two things still come from outside:

* **A python interpreter.** When the chatbox runs from source or from
  the AUR package, its own python is used. A frozen build (the Windows
  .exe) is not a python, so the plugin looks for `python3` / `python` /
  `py` on `PATH` instead and says so if there is none.
* **`zeroconf`, but only for OSCQuery.** The chatbox depends on it
  anyway, so it is normally there. If it is not, the plugin refuses that
  leash with a readable reason instead of letting OSCLeash crash on it.

The **OSCLeash override** setting stays empty for all of this. Fill it
in only to run your own build – a checkout's `OSCLeash.py`, an AppImage,
an `OSCLeash.exe` – and the folder button next to it opens a file
dialog.

## Requirements

Plugin API 2 (OSC-DreamChatbox v1.3.2 or newer). The panel is embedded
under the plugin's settings through `build_widget()`; the **🎛 Control
panel** toggle opens it in its own window instead, for a host that
cannot embed it.
