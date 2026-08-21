# OSC Parameter Profiles

Reads the avatar parameters VRChat reports, saves them under a name you
pick, and sends them back with one click.

- **Search** across profile names, categories, notes *and* parameter
  names — typing `hue` finds the profile that touches `Hue` even if it is
  called *summer*
- **Categories** you invent yourself; typing a new one in the profile
  dialog creates it
- **+ New profile** captures everything the current avatar reports
- **Load** / **Save** on every row, plus double-click to load
- Import / export as json, so a profile can be shared

## Everything runs over OSCQuery

v1.1.0 removed the fixed-port and host-event modes. There is no port to
configure any more, because every port setting was a way for the plugin
to be quietly wrong:

| Used to be | Now |
| --- | --- |
| a listen port you picked, fighting the app for 9001 | the OS picks it, mDNS announces it — a collision is impossible |
| send to 9000 and hope | read from VRChat's `HOST_INFO`, so a `--osc` launch argument cannot break it |
| wait for VRChat to push a change | pull VRChat's own parameter tree, so the list is complete immediately |
| guess the type from the packet | `TYPE` from the tree, so an Int parameter is never sent a Float |
| guess what is writable from the name | `ACCESS` from the tree — VRChat's own answer |
| assume a send that raised no error landed | read the values back and name the ones that did not take |

The name-based filter lists are still in `oscio.py`, but only as the
fallback for parameters seen before the tree was ever fetched. In the
**Live parameters** table that shows as `guess` instead of `yes`/`game`.

## First run

1. Enable the plugin and open its settings.
2. Start VRChat with OSC on (radial menu → Options → OSC → Enabled).
3. The list fills itself. If it does not, press **⟳**.

There is no "switch avatars to get a dump" step any more — the plugin
asks VRChat directly instead of waiting to be told.

## If nothing arrives

The panel puts a banner at the top saying which of these it is. Nothing
to hunt for in a log.

```fish
systemctl status avahi-daemon      # mDNS, must be running
python -c 'import zeroconf'        # required, not optional
```

VRChat under Proton is occasionally slow to notice a new mDNS service.
The plugin withdraws and re-announces itself up to three times on its own
before giving up and saying so.

`zeroconf` is a hard requirement now. On Arch/CachyOS:

```fish
sudo pacman -S python-zeroconf
```

For the AppImage it has to be bundled; the plugin says so in the banner
rather than failing silently.

## Polling

**Ask VRChat for the full list every 5 s** by default — one local HTTP
request. That is what removes the whole "nothing arrives" class of
problem: a push only happens on change, a pull happens whenever we ask.

A poll never undoes a change that raced it. A value pushed over UDP
within 0.75 s of the fetch starting wins over the HTTP response, because
VRChat renders that response at some unknown point between request and
reply — without the slack, a toggle flipped a heartbeat before a poll
would appear to bounce back on its own.

Set it to 0 to go back to push-only.

## What is not captured

Parameters VRChat marks read-only, plus — where the tree has not been
fetched yet — the built-ins (`Viseme`, `GestureLeft`, `VelocityX`,
`Grounded`, `MuteSelf`, …) and the PhysBone/Contact suffixes
(`_IsGrabbed`, `_Angle`, `_Stretch`, `_Squish`, `_Proximity`). They still
show in **Live parameters**, marked `game`, so you can see them without
them cluttering a profile.

## Sending, and the check afterwards

VRChat drops OSC packets that arrive faster than it reads them, so
parameters go out one every **8 ms** by default. Afterwards the plugin
reads the tree back and names any value that still differs — a
half-applied profile used to be indistinguishable from a working one. If
that check keeps complaining, try **Send everything 2×** before raising
the pause.

## Avatar binding

The avatar id is pulled from `/avatar/change` in the tree, not only from
a push, so the "was this captured on another avatar?" check works even
when the plugin was started mid-session. A change of avatar drops the old
avatar's parameters, so a capture can never mix two.

## Chatbox

Off by default. Turn on **Chatbox → Show the loaded profile**:

| Placeholder | Is |
| --- | --- |
| `{osc_paramprofiles}` | icon + last loaded profile |
| `{osc_paramprofiles_profile}` | just the name |
| `{osc_paramprofiles_category}` | its category |
| `{osc_paramprofiles_count}` | how many parameters are known |
| `{osc_paramprofiles_avatar}` | current avatar id |

## Where profiles live

`profiles.json` in the plugin's data dir — **⋯ → Open the profiles
folder**. One readable file, written atomically, and the plugin ships no
`configs/` folder, so an update never touches it.

Profiles from v1.0.0 load unchanged.

## v1.1.1 — the four freezes and the phantom "not running"

Four bugs, one of them the root of the others.

**"The plugin is not running" while it plainly was.** `plugin.json` says
`"main": "main.py"`, and the loader makes that file *be* the package —
that is what lets `from .panel import …` work inside it. So main.py is
registered as `osc_paramprofiles`. When panel.py did `from . import
main`, python imported main.py a **second** time as
`osc_paramprofiles.main`: a separate module object with its own globals,
all still `None`. The panel was reading a different copy of the plugin
than the one the app had called `setup()` on — which is also why it
found no parameters. All shared state now lives in `runtime.py`, a real
submodule, so both sides reach the same object no matter which file the
loader treats as the package.

**Freezing on enable, and on reconnect.** `setup()` runs on the GUI
thread, and registering an mDNS service blocks for about a second while
zeroconf probes the network for a name conflict. Two services, plus a
separate `Zeroconf()` for browsing, plus another pair on every
reconnect — each one binding its own multicast sockets and threads.
Now: the socket opens instantly on the GUI thread, everything else runs
on a worker, there is one refcounted `Zeroconf` for the whole plugin,
and `cooperating_responders` skips the probe wait that avahi is already
handling. Measured against a 10 ms heartbeat across enable, reconnect
and refresh: worst stall 33 ms, none over 60.

**No parameters.** Beyond the module-identity bug there was a real
chicken-and-egg: our advertised node tree started empty, so VRChat had
no parameter paths of ours to push to. After the first successful pull
the plugin re-announces once with the names filled in. The UDP socket
also binds `0.0.0.0` rather than `127.0.0.1`, since VRChat runs under
Proton and its idea of localhost is not always ours.

Two smaller ones found while testing: the banner reported "could not be
announced" during the second or so a re-announce takes, and a resolved
banner was never cleared because the hide was guarded on `isVisible()` —
false for a page that has not been shown yet, so a stale warning came
back the next time settings were opened.

## v1.1.2 — latency

Four complaints, three of them the same shape: the GUI thread was doing
work the user had to wait through.

**Saving.** `store.save()` did json + `fsync` + atomic replace inline.
`fsync` is a disk barrier; on btrfs with VRChat and WiVRn on the same
device it can block for over a second. The write is now handed to a
background thread — same temp-file-and-replace, same `fsync`, just not
on the click. The payload is serialised on the calling thread, so the
writer never reads the profile list while the GUI is editing it, and
repeated edits coalesce into one write.

The row appears **before** the bytes land: it shows `saving…` in place
of its Load and Save buttons until the queue drains. 22 ms from clicking
Save to seeing the row, with a 259-parameter profile.

**Loading.** The click was already non-blocking, but the row gave no
sign of it. It now shows `sending… 62%` where its buttons were, and the
buttons come back when VRChat has confirmed the values. The apply itself
is still paced at 8 ms per parameter — that pacing is what keeps VRChat
from dropping toggles, so it stays.

**The live table** was cleared and rebuilt from scratch whenever any
value changed — which, while a profile is being sent, means every 250 ms
for every parameter VRChat echoes back. Now rows are only rebuilt when
the set of parameter *names* changes; a value change is three `setText`
calls. 0.68 ms per update instead of a full rebuild.

**Opening the profile dialog** on a 260-parameter avatar took 181 ms,
because items were added one at a time with signals live. Batched into
one `addTopLevelItems` with signals blocked: 20 ms.

Also new: a **Refresh** button next to the Live parameters header, which
pulls VRChat's full tree immediately instead of waiting for the next
poll, and a live count in the header itself.

Measured against a 10 ms heartbeat across a full save-and-load cycle:
worst GUI stall 11 ms, none over 60.

## Files

| File | What |
| --- | --- |
| `main.py` | the hook surface, and nothing else — holds no state |
| `runtime.py` | all shared state, startup threading, watchdog, apply, verify |
| `oscio.py` | OSC codec, UDP socket, our advertised OSCQuery service |
| `discovery.py` | finds VRChat's OSCQuery service and reads its tree |
| `store.py` | profiles.json, categories, import/export |
| `panel.py` | the panel |
| `dialogs.py` | new / edit profile, categories |

`oscio.py` implements OSC itself rather than depending on `python-osc`
being importable from inside a plugin. Threads never touch widgets: the
receive loop, the watchdog and the apply worker all write to plain
python state, and the panel picks it up from a `QTimer`.
