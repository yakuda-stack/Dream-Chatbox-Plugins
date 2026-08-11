"""The rules, and the loop that acts on them.

One :class:`Rule` is "when *these* programs run, start *those*". It owns
its triggers, its targets and its own little state machine; the
:class:`Engine` owns the list of rules, the file they live in and the one
thread that ticks them.

The state machine is deliberately boring, because the interesting part is
what happens at the edges rather than in the middle:

    idle  --trigger appears-->  active  (targets start, each after its
                                         own delay)
    active --trigger gone-->    losing  (a grace period, so alt-tabbing
                                         out of a game that restarts its
                                         own process does not tear the
                                         whole set down)
    losing --grace over-->      idle    (targets with "stop again" stop)
    losing --trigger back-->    active  (nothing happens: the targets
                                         never stopped)

The grace period is the reason this is not three lines. SteamVR restarts
``vrserver`` during a headset reconnect, VRChat's process disappears for a
moment on a world crash, and a Proton game changes its process name while
it boots. Without a grace period every one of those looks like "quit" and
kills the overlays the user is standing in.

Nothing in here imports Qt. The panel polls it from the GUI thread.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import os
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from . import procs
from .launcher import Target, new_target
from .oscleash_link import OSCLeashLink

MAX_EVENTS = 300
RESTART_DELAY = 5.0

RULE_DEFAULTS = {
    "name": "Rule",
    "enabled": True,
    # kept for configs written before 1.2.0: it is read once, turned into
    # a per-trigger mode and never used again
    "match": "any",
    "grace": 10,            # seconds the trigger may be gone before stopping
}

# Per trigger, since 1.2.0. "must" is an AND, "one_of" is an OR across
# every row that carries it - which is what a VR setup actually needs:
# VRChat *and* the runtime, where the runtime may be WiVRn or SteamVR.
MUST = "must"
ONE_OF = "one_of"
MODE_LABELS = {MUST: "must run", ONE_OF: "one of these"}

# What people actually trigger on. The value is a ``@key`` from
# procs.SMART rather than a process name, because a process name is a
# guess: WiVRn is a systemd unit as often as it is a process, VRChat is
# called VRChat.exe even on Linux, and none of that is something anyone
# should have to know to fill in a dropdown.
PRESETS = [
    ("VRChat", "@vrchat"),
    ("SteamVR", "@steamvr"),
    ("WiVRn server", "@wivrn"),
    ("Monado", "@monado"),
    ("ALVR", "@alvr"),
    ("SlimeVR", "@slimevr"),
    ("WlxOverlay-S", "@wlx"),
    ("WayVR Dashboard", "@wayvr"),
    ("VRCX", "@vrcx"),
    ("Steam", "@steam"),
    ("Resonite", "@resonite"),
    ("ChilloutVR", "@chilloutvr"),
]

# the ones people end up asking about, shown as a status strip above the
# rules so the answer is there before anyone builds a rule around it.
# WiVRn and Monado are Linux runtimes and would be four permanently grey
# dots on Windows, so the strip differs per platform.
WATCHED = (["@vrchat", "@steamvr", "@steam", "@vrcx"] if os.name == "nt"
           else ["@wivrn", "@vrchat", "@steamvr", "@monado"])


def pattern_label(pattern):
    """What to call a trigger in a sentence.

    A ``@key`` knows its own name, a ``check:`` command is only ever
    going to be called "the command", and anything else is what the user
    typed - which is already the clearest thing available.
    """
    raw = str(pattern or "").strip()
    if raw.startswith("@"):
        return procs.smart_label(raw[1:])
    if raw.lower().startswith("check:"):
        command = raw[6:].strip()
        return f"`{command[:28]}`" if command else "a command"
    return raw


def new_rule(name=""):
    data = dict(RULE_DEFAULTS)
    data["id"] = uuid.uuid4().hex[:8]
    data["name"] = name or RULE_DEFAULTS["name"]
    data["triggers"] = [{"id": uuid.uuid4().hex[:8], "pattern": "",
                         "mode": MUST}]
    data["targets"] = [new_target("Program 1")]
    return data


class Rule:
    """One trigger set and the programs that hang off it."""

    def __init__(self, data, log=None, link=None):
        self.data = dict(RULE_DEFAULTS, **(data or {}))
        self.data.setdefault("id", uuid.uuid4().hex[:8])
        self.log = log or (lambda _m: None)
        self.link = link
        raw_targets = self.data.get("targets") or []
        self.targets = [Target(t, log) for t in raw_targets
                        if isinstance(t, dict)]
        for target in self.targets:
            target.link = link
        if not isinstance(self.data.get("triggers"), list):
            self.data["triggers"] = []
        self._migrate_modes()
        self.state = "idle"          # idle | active | losing
        self.missing = []            # labels of what is keeping it waiting
        self.lost_at = 0.0
        self.hit_at = 0.0
        self.manual = False          # started by the button, not a trigger

    # ------------------------------------------------------------ ids
    @property
    def rid(self):
        return self.data["id"]

    @property
    def name(self):
        return str(self.data.get("name") or "").strip() or "Rule"

    def get(self, key):
        return self.data.get(key, RULE_DEFAULTS.get(key))

    def set(self, key, value):
        self.data[key] = value

    def export(self):
        out = dict(self.data)
        out["targets"] = [t.data for t in self.targets]
        return out

    # -------------------------------------------------------- triggers
    @property
    def triggers(self):
        return [t for t in self.data.get("triggers", []) if isinstance(t, dict)]

    def _migrate_modes(self):
        """Give every trigger row a mode, from the old rule-wide one.

        A config written before 1.2.0 has "match": "any" or "all" on the
        rule and nothing on the rows. Turning that into per-row modes
        here means an existing setup keeps behaving exactly as it did,
        and nobody has to go and re-tick anything.
        """
        old = ONE_OF if str(self.data.get("match", "any")) != "all" else MUST
        for row in self.triggers:
            if row.get("mode") not in (MUST, ONE_OF):
                row["mode"] = old

    def add_trigger(self, pattern="", mode=MUST):
        """A new row is a "must" on purpose: adding a second trigger
        almost always means "and this one too" - VRChat and WiVRn, not
        VRChat or WiVRn."""
        row = {"id": uuid.uuid4().hex[:8], "pattern": pattern, "mode": mode}
        self.data.setdefault("triggers", []).append(row)
        return row

    def split(self):
        """(must, one_of) - the patterns of both groups, empties dropped."""
        must, options = [], []
        for row in self.triggers:
            pattern = str(row.get("pattern") or "").strip()
            if not pattern:
                continue
            (must if row.get("mode", MUST) == MUST else options).append(pattern)
        return must, options

    def condition_text(self):
        """The rule as a sentence, for the panel and for the log."""
        must, options = self.split()
        parts = [pattern_label(p) for p in must]
        if options:
            names = ", ".join(pattern_label(p) for p in options)
            parts.append(f"one of ({names})" if len(options) > 1 else names)
        if not parts:
            return ""
        if len(parts) == 1:
            return parts[0]
        return " and ".join((", ".join(parts[:-1]), parts[-1]))

    def remove_trigger(self, tid):
        rows = self.data.get("triggers", [])
        self.data["triggers"] = [r for r in rows if r.get("id") != tid]

    def patterns(self):
        return [str(t.get("pattern") or "").strip() for t in self.triggers
                if str(t.get("pattern") or "").strip()]

    # --------------------------------------------------------- targets
    def add_target(self, name="", kind="path"):
        target = Target(new_target(name or f"Program {len(self.targets) + 1}",
                                   kind), self.log)
        target.link = self.link
        self.targets.append(target)
        return target

    def remove_target(self, tid):
        for target in list(self.targets):
            if target.tid == tid:
                target.stop()
                self.targets.remove(target)
                return True
        return False

    def own_pids(self):
        pids = set()
        for target in self.targets:
            if target.pid:
                pids.add(target.pid)
            pids.update(target.kids)
        return pids

    # ----------------------------------------------------------- match
    def evaluate(self, snap, ignore_pids):
        """Is the trigger condition true right now?

        Every "must run" row has to be running, and – when there are any
        – at least one "one of these" row on top. Two musts is the case
        this exists for: VRChat *and* WiVRn, so an overlay does not come
        up while only the runtime is warming, and does not stay up when
        only the runtime is left.

        Also records *what* is missing, because "waiting for the trigger"
        is a useless thing to read when two of them are involved.

        No patterns means no trigger, which means the rule can only be
        run by hand – that is a legitimate setup ("my VR set", one
        button) and not an error.
        """
        must, options = self.split()
        if not must and not options:
            self.missing = []
            return False

        missing = [pattern_label(p) for p in must
                   if not procs.is_running(p, ignore_pids, snap,
                                           allow_run=True)]
        have_option = not options or any(
            procs.is_running(p, ignore_pids, snap, allow_run=True)
            for p in options)
        if not have_option:
            names = ", ".join(pattern_label(p) for p in options)
            missing.append(f"one of ({names})" if len(options) > 1 else names)
        self.missing = missing
        return not missing

    def running_targets(self):
        return sum(1 for t in self.targets if t.running)

    def state_text(self):
        if not self.get("enabled"):
            return "disabled"
        running = self.running_targets()
        total = sum(1 for t in self.targets if t.get("enabled"))
        if self.state == "active":
            how = "by hand" if self.manual else "triggered"
            return f"{how} \u00b7 {running}/{total} running"
        if self.state == "losing":
            gone = ", ".join(self.missing) or "the trigger"
            left = self.grace_left()
            return (f"{gone} gone \u00b7 stopping in {left}s"
                    if left else f"{gone} gone \u00b7 stopping")
        if not self.patterns():
            return "no trigger \u00b7 manual only"
        if self.missing:
            # the whole point of naming it: with two triggers, "waiting"
            # alone never says which half is missing
            return "waiting for " + ", ".join(self.missing)
        return "waiting for the trigger"

    def grace_secs(self):
        try:
            return max(0, int(self.get("grace") or 0))
        except (TypeError, ValueError):
            return 10

    def grace_left(self):
        if self.state != "losing" or not self.lost_at:
            return 0
        return max(0, int(round(self.grace_secs()
                                - (time.time() - self.lost_at))))

    # --------------------------------------------------------- control
    def fire(self, manual=False):
        """Schedule every enabled target. Each one starts after its own
        delay, which is what lets a set come up in order: SteamVR first,
        the overlay ten seconds later, once there is something to overlay
        onto."""
        now = time.time()
        self.state = "active"
        self.manual = manual
        self.hit_at = now
        self.lost_at = 0.0
        for target in self.targets:
            if not target.get("enabled"):
                continue
            if target.running:
                continue
            try:
                delay = max(0, int(target.get("delay") or 0))
            except (TypeError, ValueError):
                delay = 0
            target.due_at = now + delay
            target.error = ""

    def release(self, force=False):
        """Trigger is gone: stop what asked to be stopped again."""
        for target in self.targets:
            target.due_at = 0.0
            if force or target.get("stop_with"):
                target.stop()
        self.state = "idle"
        self.manual = False
        self.lost_at = 0.0

    def stop_all_targets(self):
        for target in self.targets:
            target.due_at = 0.0
            target.stop()
        self.state = "idle"
        self.manual = False


class Engine:
    """The rule list, its file, and the single thread that ticks it."""

    def __init__(self, data_dir, log, settings):
        self.dir = Path(data_dir)
        self.log = log
        self.settings = settings          # callable: (key, default) -> value
        self.file = self.dir / "rules.json"
        self.link = OSCLeashLink(self._event)
        self.rules = []
        self.armed = False
        self.events = deque(maxlen=MAX_EVENTS)
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self.load()

    # ----------------------------------------------------- persistence
    def load(self):
        raw = []
        try:
            if self.file.is_file():
                raw = json.loads(self.file.read_text(encoding="utf-8"))
        except Exception as e:
            self._event(f"rules.json unreadable ({e}) – starting empty")
            raw = []
        if not isinstance(raw, list):
            raw = []
        self.rules = [Rule(d, self._event, self.link)
                      for d in raw if isinstance(d, dict)]
        if not self.rules:
            # a first rule that already says something: an empty panel
            # with a blank trigger box explains nothing about what the
            # box wants, and "vrchat.exe|vrchat" does
            first = new_rule("VRChat")
            first["triggers"][0]["pattern"] = PRESETS[0][1]
            self.rules = [Rule(first, self._event, self.link)]
            self.save()

    def save(self):
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self.file.write_text(
                json.dumps([r.export() for r in self.rules], indent=2),
                encoding="utf-8")
        except OSError as e:
            self._event(f"could not save rules.json: {e}")

    # ------------------------------------------------------- list ops
    def add_rule(self, name=""):
        rule = Rule(new_rule(name or f"Rule {len(self.rules) + 1}"),
                    self._event, self.link)
        self.rules.append(rule)
        self.save()
        return rule

    def remove_rule(self, rid):
        rule = self.by_id(rid)
        if rule is None:
            return False
        rule.stop_all_targets()
        self.rules.remove(rule)
        self.save()
        return True

    def by_id(self, rid):
        for rule in self.rules:
            if rule.rid == rid:
                return rule
        return None

    # ---------------------------------------------------------- events
    def _event(self, text):
        text = str(text).strip()
        if not text:
            return
        self.events.append(f"{time.strftime('%H:%M:%S')}  {text}")
        try:
            self.log(text)
        except Exception:
            pass

    def event_text(self):
        return "\n".join(self.events)

    def clear_events(self):
        self.events.clear()

    # ----------------------------------------------------- big buttons
    def arm(self):
        """Watch the triggers. Anything already running stays running."""
        if self.armed:
            return
        self.armed = True
        self._event("autostart armed")
        self.start_thread()

    def disarm(self, stop_targets=True):
        """Stop watching – and, by default, stop everything that was
        started from here. That is the whole point of the Stop button:
        one press and the set is down again."""
        was = self.armed
        self.armed = False
        if stop_targets:
            for rule in self.rules:
                rule.stop_all_targets()
        if was:
            self._event("autostart stopped"
                        if stop_targets else "autostart disarmed")

    def running_count(self):
        return sum(r.running_targets() for r in self.rules)

    def target_count(self):
        return sum(len(r.targets) for r in self.rules)

    def active_rule(self):
        for rule in self.rules:
            if rule.state == "active":
                return rule
        return None

    def own_pids(self):
        pids = set()
        for rule in self.rules:
            pids |= rule.own_pids()
        return pids

    # ------------------------------------------------------- manual run
    def run_rule(self, rid):
        rule = self.by_id(rid)
        if rule is None:
            return "unknown rule"
        rule.fire(manual=True)
        self._event(f"{rule.name}: started by hand")
        self.tick(force=True)
        return ""

    def stop_rule(self, rid):
        rule = self.by_id(rid)
        if rule is not None:
            rule.stop_all_targets()
            self._event(f"{rule.name}: stopped by hand")

    # ------------------------------------------------------------ loop
    def _setting(self, key, default):
        try:
            return self.settings(key, default)
        except Exception:
            return default

    def poll_secs(self):
        try:
            return max(1, int(self._setting("poll_secs", 2)))
        except (TypeError, ValueError):
            return 2

    def start_thread(self):
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop,
                                        name="vr-autostart", daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as e:
                self._event(f"watcher: {e}")
            self._stop.wait(self.poll_secs())

    def tick(self, force=False):
        """One pass: pending starts, dead children, trigger changes."""
        with self._lock:
            now = time.time()
            snap = procs.snapshot(force=force)
            ignore = self.own_pids()
            restart = bool(self._setting("restart_crashed", False))

            # keep the answers for the status strip warm: the panel is
            # not allowed to spawn systemctl from the GUI thread, so the
            # watcher thread refreshes them here and the panel reads the
            # cache
            for key in WATCHED:
                procs.probe(key, ignore, snap, allow_run=True)

            for rule in self.rules:
                # a target that ended on its own – noticed here so the
                # panel does not keep claiming it runs
                for target in rule.targets:
                    if target.reap():
                        self._event(f"{target.name}: ended")
                        if restart and rule.state == "active" \
                                and target.get("enabled"):
                            target.due_at = now + RESTART_DELAY
                    else:
                        target.note_children()

                if not rule.get("enabled"):
                    if rule.state != "idle":
                        rule.release()
                    continue

                if self.armed:
                    self._follow_trigger(rule, snap, ignore, now)

                # due starts, whether from a trigger or from the button
                for target in rule.targets:
                    if target.due_at and now >= target.due_at:
                        target.due_at = 0.0
                        err = target.start()
                        if err:
                            self._event(f"{target.name}: {err}")

    def _follow_trigger(self, rule, snap, ignore, now):
        hit = rule.evaluate(snap, ignore)
        if hit:
            if rule.state == "idle":
                count = sum(1 for t in rule.targets if t.get("enabled"))
                rule.fire()
                self._event(f"{rule.name}: {rule.condition_text()} running "
                            f"– starting {count} program(s)")
            elif rule.state == "losing":
                # came back inside the grace window: nothing was stopped,
                # so there is nothing to start either
                self._event(f"{rule.name}: back before the grace ran out "
                            f"– nothing stopped")
                rule.state = "active"
                rule.lost_at = 0.0
            return

        if rule.state == "active" and not rule.manual:
            rule.state = "losing"
            rule.lost_at = now
            # named here rather than at the end of the grace: this is the
            # moment it actually happened, and by the time the grace runs
            # out a second one may be gone too
            gone = ", ".join(rule.missing) or "the trigger"
            self._event(f"{rule.name}: {gone} gone – stopping in "
                        f"{rule.grace_secs()}s unless it comes back")
            return
        if rule.state == "losing":
            if now - rule.lost_at >= rule.grace_secs():
                gone = ", ".join(rule.missing) or "the trigger"
                self._event(f"{rule.name}: {gone} still gone – stopping")
                rule.release()

    # -------------------------------------------------------- shutdown
    def shutdown(self, stop_targets=True):
        self._stop.set()
        self._thread = None
        if stop_targets:
            for rule in self.rules:
                rule.stop_all_targets()
        self.armed = False
        self.save()
