# VR Autostart

One press instead of six. Pick what has to be running — VRChat, SteamVR,
WiVRn, anything with a process name — and pick what should come up with
it: a `.sh`, an AppImage, a binary, a `.py`, on Windows an `.exe`, `.bat`,
`.cmd` or a shortcut, a command line exactly as a terminal would take it,
or the **OSCLeash plugin**, which is started through its own manager
rather than as a second process.

Then press **START AUTOSTART** once and forget about it. When the trigger
appears the set comes up, when it disappears the set goes down again.
**STOP AUTOSTART** stops the watcher *and* everything it started.

## The two big buttons

| | |
| --- | --- |
| **START AUTOSTART** | Watch the triggers. Nothing that is already running is touched. |
| **STOP AUTOSTART** | Stop watching, and stop every program this plugin started — in one press. |

Programs that were running before, or that you started yourself, are
never stopped. The plugin only ever takes down what it brought up.

## A rule

A rule is one sentence: *when these run, start those.*

**Start when this runs** — one or more triggers, each one a row with a
dropdown, a folder button and a status LED.

The dropdown is a list of *programs*, not of process names: VRChat,
SteamVR, WiVRn server, Monado, ALVR, SlimeVR, WlxOverlay-S, WayVR
Dashboard, VRCX, Steam, Resonite, ChilloutVR. Nobody should have to know
that SteamVR is called `vrmonitor`, and for WiVRn there is no single
right answer at all — it is a process when it was started from a
terminal and a systemd user unit when it was started the usual way. So
picking an entry stores a key like `@wivrn`, and the plugin works out how
to ask: process list first, then the runtime's own IPC socket
(connected to, not just checked for — a crashed WiVRn leaves its socket
file behind), then `systemctl --user is-active`. The LED says which of
the three answered.

The last two entries in the dropdown are for everything else:

* **Own program / name** — type a piece of a process name, or press the
  📁 button next to the dropdown and pick an AppImage, a `.sh`, a binary
  or an `.exe`. The *file name* is stored, not the path: the same program
  started from Steam, from a `.desktop` file or from another folder is
  still the same program. Several alternatives with `|` between them.
* **Terminal command** — a command whose exit code answers the question.
  `pgrep -f wivrn-server`, `systemctl --user is-active monado`,
  `pidof wlx-overlay-s`, anything at all. Exit code 0 counts as running.

The strip under the two big buttons shows WiVRn, VRChat, SteamVR and
Monado at a glance, answered exactly the way a trigger is answered — so
when a rule does not fire, the first question ("is the thing I am waiting
for actually running?") is already on screen. **Check again** asks
immediately instead of waiting for the next poll.

### Two triggers at once

Every trigger row carries its own mode, so a rule reads as one sentence
instead of an all-or-nothing switch:

* **must run** — the rule only fires while this one is running. Two of
  these is the usual VR case: **WiVRn and VRChat**, so the overlays do
  not come up while only the runtime is warming, and do not stay up when
  the game is gone and only the runtime is left. A new trigger row is a
  *must run*, because adding a second trigger nearly always means "and
  this one too".
* **one of these** — counts together with every other row set the same
  way, and one of them is enough. For the runtime that is WiVRn today and
  SteamVR tomorrow, or for the same overlays across VRChat and Resonite.

The two mix. *VRChat must run, and one of WiVRn or SteamVR* is one row on
**must run** and two on **one of these** — the musts are all required,
the rest count as one alternative between them. The line under the
triggers always spells out what the rule currently means:

```
Starts when VRChat and one of (WiVRn, SteamVR) are running · still waiting for VRChat
```

It updates live, in the same rhythm as the LEDs next to the rows, and it
names the half that is missing — which is the thing "waiting for the
trigger" never told anyone. The rule's own status line and the log say
the same: *WiVRn gone · stopping in 10s*, and *back before the grace ran
out — nothing stopped* when it reappears in time.

Rules written before 1.2.0 keep working untouched: the old rule-wide
*any* becomes *one of these* on every row, *all* becomes *must run*, and
nothing has to be re-ticked.

**Then start these** — as many programs as you want, `＋ Program` for one
more. Each one has:

| Field | What it does |
| --- | --- |
| kind | *Program / file* (file dialog), *Command* (typed, shell syntax allowed), *OSCLeash plugin* |
| arguments | passed to the program, quoted the way a shell would |
| start after | seconds between the trigger and this program — this is what gives a set an order |
| stop again | take it down when the trigger is gone. Off leaves it up for the session |
| skip if already running | do not start a second copy of something that is already up |
| stop command | run this instead of killing the process, for a program that wants to be shut down its own way |

**stop after** on the rule is the grace period: how long the trigger may
be gone before anything is stopped. It defaults to ten seconds and it is
not padding. A game restarting its own process, a headset reconnect and a
world crash all look like *quit* for a moment, and without a grace period
each of them tears down the overlay you are standing in.

Every row has a `▶` of its own, so a rule can be tested without waiting
for a game to boot.

## OSCLeash

Pick **OSCLeash plugin** as the kind and the target starts and stops every
leash configured in the [OSCLeash plugin](https://github.com/yakuda-stack),
through that plugin's own manager. That matters: OSCLeash already has a
supervisor — instance list, generated configs, restart-on-crash watchdog,
per-instance debug console. Starting `OSCLeash.py` from here a second time
would give you two processes fighting over port 9001, one of them
invisible.

The OSCLeash plugin has to be installed and switched on. When it is not,
the target says so in the panel and the rest of the rule carries on
without it. There is no dependency in either manifest and no import
between the two — this plugin finds the neighbour at runtime or does not.

## Command examples

```
wlx-overlay-s --show
flatpak run io.github.example.App
sh -c "sleep 5 && wivrn-server"
env WLR_RENDERER=vulkan wayvr-dashboard
```

Anything with a pipe, `&&`, a variable or a wildcard is handed to a shell,
so it behaves the way it does in a terminal. Anything simpler is started
directly, which means no shell is in the way when it has to be stopped.

## What it starts, and how it stops it

Every child gets its own session (Linux) or process group (Windows), so
stopping a target takes its whole tree with it. That is the difference
between stopping a program and stopping the `.sh` that started it and
leaving the program running forever.

`stdout` goes to `/dev/null` on purpose. These are games and overlays, not
services; they print megabytes, and a pipe nobody reads eventually fills
up and blocks the child that writes to it.

## Windows

Everything works the same way, with the parts that have no Windows
equivalent quietly dropped: the status strip shows VRChat, SteamVR, Steam
and VRCX instead of the Linux runtimes, and the IPC-socket and systemd
questions are never asked because WiVRn and Monado do not exist there.

* the file dialog picks `.exe`, `.bat`, `.cmd`, `.lnk` and `.py`. A `.lnk`
  is not an executable, so it is opened through the shell — that is the
  only thing that resolves a shortcut without COM.
* a **Terminal command** trigger runs through `cmd /c`, so it is
  `tasklist /FI "IMAGENAME eq wivrn.exe" | find /I "wivrn"` or a
  PowerShell one-liner rather than `pgrep`.
* the process list comes from `CreateToolhelp32Snapshot`, with `tasklist`
  as a fallback. Neither gives command lines, so a Windows trigger matches
  on the executable name — which is what people type there anyway.
  Install `psutil` and command lines come back.
* every child gets its own process group and is stopped with
  `taskkill /T`, so a launcher that spawned the real program does not
  leave it behind.
* no console window flashes up: children are started detached and every
  helper call carries `CREATE_NO_WINDOW`.

## Placeholders

```
{vr_autostart}          🚀 VRChat · 3 running
{vr_autostart_state}    running | armed | off
{vr_autostart_rule}     name of the rule that is active
{vr_autostart_count}    how many programs this plugin has running
{vr_autostart_targets}  how many are configured in total
```

The chatbox line is off by default — the plugin says nothing until
**Chatbox line → Say something in the chatbox** is switched on. The
placeholders work either way.

## Where things are

Rules live in the plugin's own data folder as `rules.json`, next to the
app config. Nothing is written anywhere else, nothing is installed, and
removing the plugin removes exactly this plugin.

The process list is read through `psutil` when it happens to be
installed, otherwise from `/proc` on Linux and through
`CreateToolhelp32Snapshot` on Windows, with `tasklist` as a last resort.
The panel says which one is in use. No optional dependency is required
for any of it.

## Two things worth knowing

**systemctl is never run from the GUI thread.** The watcher thread
refreshes the systemd and command-check answers and caches them; the
panel only ever reads that cache. A LED that is two seconds behind is
fine, a window that freezes for two seconds is not.

**A trigger cannot be satisfied by its own targets.** Everything this
plugin started is excluded from the process scan, so a rule that starts a
program whose name matches its own trigger does not lock itself on.

**`skip if already running` matches differently per kind.** A picked file
matches on its file name, which is specific enough. A typed command
matches on the whole line, because the first word of
`flatpak run com.example.App` is `flatpak`, and matching on that would
call every Flatpak on the system "already running".
