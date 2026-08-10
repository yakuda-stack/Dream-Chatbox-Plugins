"""The Qt side of VR Autostart.

    AutostartPanel   the two big buttons, the rule list, the log
    RuleCard         one rule: triggers, programs, its own Run/Stop
    TriggerRow       one "this has to be running" line
    TargetRow        one "start this" line
    EventWindow      what the engine did, in its own window

Everything reads from :mod:`engine` through a single timer instead of
through signals out of the watcher thread. That is not style, it is the
one rule that matters here: a Qt widget touched from a non-GUI thread is
a SIGSEGV, and a poll on the GUI thread cannot be one.

The object names (card, cardtitle, dim, iconbtn, hline) are the app's
own, so the panel inherits the stylesheet instead of inventing a second
look.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import os
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QGuiApplication
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
    QVBoxLayout, QWidget)

from . import procs
from .engine import PRESETS, WATCHED

IS_WINDOWS = os.name == "nt"
POLL_MS = 600
LOG_MS = 400

GREEN = "#4f9d69"
RED = "#b5504a"
GREY = "#4a5060"
AMBER = "#d9884a"
BLUE = "#5b8dc9"

BTN = ("QPushButton {{ background: {bg}; border: 1px solid {edge};"
       " border-radius: 8px; color: {fg}; padding: 0 12px; }}"
       "QPushButton:hover {{ background: {hover}; }}"
       "QPushButton:disabled {{ background: #23262f; border-color: #333947;"
       " color: #666d7a; }}")

KINDS = [("path", "Program / file"), ("command", "Command"),
         ("oscleash", "OSCLeash plugin")]

FILE_FILTER = ("Programs (*.exe *.bat *.cmd *.lnk *.py);;All files (*)"
               if IS_WINDOWS else
               "Programs (*.sh *.AppImage *.appimage *.py *.run *.bash);;"
               "All files (*)")


def _btn_style(bg, edge, fg="#e6ecf5", hover=None):
    return BTN.format(bg=bg, edge=edge, fg=fg, hover=hover or edge)


def _small(text, tip=""):
    label = QLabel(text)
    label.setObjectName("dim")
    if tip:
        label.setToolTip(tip)
    return label


def _button(text, style, tip="", height=30):
    btn = QPushButton(text)
    btn.setFixedHeight(height)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(style)
    if tip:
        btn.setToolTip(tip)
    return btn


def _icon_button(glyph, tip):
    btn = QPushButton(glyph)
    btn.setObjectName("iconbtn")
    btn.setFixedSize(28, 28)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setToolTip(tip)
    return btn


class EventWindow(QDialog):
    """What the engine did and when. Non-modal: people watch this while
    a game is booting, which is exactly when it is interesting."""

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle("VR Autostart – log")
        self.resize(700, 400)
        self.setStyleSheet("QDialog { background: #14161c; }"
                           "QLabel { color: #b9c2d0; }")

        box = QVBoxLayout(self)
        box.setContentsMargins(12, 12, 12, 12)
        box.setSpacing(8)

        head = QHBoxLayout()
        head.addWidget(_small("Every start, every stop, every trigger."))
        head.addStretch()
        for text, slot in (("Copy", self.copy_all), ("Clear", self.clear_log)):
            btn = _button(text, _btn_style("#232733", "#333947", "#cfd6e2",
                                           "#2c3140"), height=28)
            btn.clicked.connect(slot)
            head.addWidget(btn)
        box.addLayout(head)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(500)
        self.view.setFont(QFont("monospace", 10))
        self.view.setStyleSheet(
            "QPlainTextEdit { background: #0f1116; color: #c8d2e0;"
            " border: 1px solid #333947; border-radius: 8px; padding: 6px; }")
        box.addWidget(self.view, 1)

        self._shown = ""
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(LOG_MS)
        self.refresh()

    def refresh(self):
        text = self.engine.event_text()
        if text != self._shown:
            self._shown = text
            bar = self.view.verticalScrollBar()
            at_end = bar.value() >= bar.maximum() - 4
            self.view.setPlainText(text)
            if at_end:
                self.view.verticalScrollBar().setValue(
                    self.view.verticalScrollBar().maximum())

    def copy_all(self):
        QGuiApplication.clipboard().setText(self.engine.event_text())

    def clear_log(self):
        self.engine.clear_events()
        self._shown = ""
        self.view.clear()

    def closeEvent(self, event):
        self.timer.stop()
        super().closeEvent(event)


class TriggerRow(QWidget):
    """One "this has to be running" line.

    The dropdown is a list of programs, not of process names. Nobody
    should have to know that SteamVR is called ``vrmonitor`` or that
    WiVRn is a systemd unit half the time – picking "WiVRn server"
    stores ``@wivrn``, and :mod:`procs` works out how to ask.

    The two entries at the bottom of the list are for everything else:
    a program picked with the folder button next to the dropdown (an
    AppImage, a .sh, a binary), or a terminal command whose exit code
    answers the question – ``pgrep -f foo``, ``systemctl --user
    is-active bar``, ``pidof baz``.
    """

    FILE_MODE = "__file__"
    CHECK_MODE = "__check__"

    def __init__(self, card, data):
        super().__init__()
        self.card = card
        self.data = data
        self._loading = True

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        self.led = QLabel("\u25CF")
        self.led.setFixedWidth(14)
        row.addWidget(self.led)

        self.combo = QComboBox()
        self.combo.setMinimumWidth(210)
        self.combo.setToolTip(
            "Pick the program that has to be running. The list knows how "
            "to find each one \u2013 process, IPC socket or systemd unit, "
            "whichever answers.\n\nThe last two entries are for anything "
            "not in the list.")
        for label, pattern in PRESETS:
            self.combo.addItem(label, pattern)
        self.combo.insertSeparator(self.combo.count())
        self.combo.addItem("Own program / name\u2026", self.FILE_MODE)
        self.combo.addItem("Terminal command\u2026", self.CHECK_MODE)
        row.addWidget(self.combo)

        self.btn_pick = _icon_button(
            "\U0001F4C1",
            "Pick a program: an AppImage, a .sh, a binary or an .exe. Its "
            "file name becomes what this trigger looks for.")
        self.btn_pick.clicked.connect(self.on_pick)
        row.addWidget(self.btn_pick)

        self.text = QLineEdit()
        self.text.setToolTip(
            "What to look for: part of a process name or of its command "
            "line, several alternatives with | between them. In command "
            "mode this is the command instead \u2013 exit code 0 counts "
            "as running.")
        self.text.editingFinished.connect(self.on_text)
        row.addWidget(self.text, 1)

        self.state = _small("")
        self.state.setMinimumWidth(150)
        row.addWidget(self.state)

        self.btn_del = _icon_button("\U0001F5D1", "Remove this trigger")
        self.btn_del.clicked.connect(self.on_delete)
        row.addWidget(self.btn_del)

        self.load()
        self._loading = False
        self.combo.currentIndexChanged.connect(self.on_mode)

    # ------------------------------------------------------------ data
    def pattern(self):
        return str(self.data.get("pattern") or "").strip()

    def load(self):
        """Work the stored pattern back into a mode plus a text."""
        raw = self.pattern()
        index = self.combo.findData(raw)
        if raw and index >= 0:
            self.combo.setCurrentIndex(index)
            self.text.setText("")
        elif raw.lower().startswith("check:"):
            self.combo.setCurrentIndex(self.combo.findData(self.CHECK_MODE))
            self.text.setText(raw[6:].strip())
        else:
            self.combo.setCurrentIndex(self.combo.findData(self.FILE_MODE))
            self.text.setText(raw)
        self.apply_mode()

    def mode(self):
        value = self.combo.currentData()
        return value if value in (self.FILE_MODE, self.CHECK_MODE) else "preset"

    def apply_mode(self):
        mode = self.mode()
        custom = mode != "preset"
        self.text.setVisible(custom)
        self.btn_pick.setVisible(mode == self.FILE_MODE)
        if mode == self.CHECK_MODE:
            self.text.setPlaceholderText(
                "pgrep -f wivrn-server   \u00b7   systemctl --user is-active "
                "monado")
        else:
            self.text.setPlaceholderText(
                "process name, e.g. wlx-overlay-s   \u00b7   several with |")

    def store(self):
        mode = self.mode()
        if mode == "preset":
            value = str(self.combo.currentData() or "")
        elif mode == self.CHECK_MODE:
            command = self.text.text().strip()
            value = f"check:{command}" if command else ""
        else:
            value = self.text.text().strip()
        if value != self.data.get("pattern"):
            self.data["pattern"] = value
            self.card.save()

    # --------------------------------------------------------- actions
    def on_mode(self, _index):
        if self._loading:
            return
        self.apply_mode()
        self.store()

    def on_text(self):
        if not self._loading:
            self.store()

    def on_pick(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Pick the program to watch for", str(Path.home()),
            FILE_FILTER)
        if not path:
            return
        # the file name, not the path: the same program started from a
        # different folder, from Steam or from a .desktop file is still
        # the same program, and the path would only match one of them
        from .launcher import base_name
        self.text.setText(base_name(path))
        self.combo.setCurrentIndex(self.combo.findData(self.FILE_MODE))
        self.apply_mode()
        self.store()

    def on_delete(self):
        self.card.remove_trigger(self)

    # ----------------------------------------------------------- state
    def sync(self, snap, ignore):
        pattern = self.pattern()
        if not pattern:
            self.led.setStyleSheet(f"color: {GREY};")
            self.state.setText("nothing picked")
            return
        running, how = procs.probe(pattern, ignore, snap)
        self.led.setStyleSheet(f"color: {GREEN if running else GREY};")
        self.state.setText(how if running else "not running")
        self.state.setToolTip(f"looking for: {pattern}")


class RuntimeStrip(QWidget):
    """WiVRn, VRChat, SteamVR, Monado – running or not, at a glance.

    Answered the same way a trigger is, so what the strip says is
    exactly what a rule would see. It is here because it is the first
    question anyone has when a rule does not fire: is the thing I am
    waiting for actually running, or is my trigger wrong?
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(14)
        self.items = []
        for key in WATCHED:
            led = QLabel("\u25CF")
            led.setFixedWidth(12)
            label = _small(procs.smart_label(key.lstrip("@")))
            row.addWidget(led)
            row.addWidget(label)
            self.items.append((key, led, label))
        row.addStretch()

    def sync(self, snap, ignore):
        for key, led, label in self.items:
            running, how = procs.probe(key, ignore, snap)
            led.setStyleSheet(f"color: {GREEN if running else GREY};")
            name = procs.smart_label(key.lstrip("@"))
            label.setText(name)
            label.setToolTip(f"{name}: {how}" if running
                             else f"{name}: not running")


class TargetRow(QFrame):
    """One program that the rule starts."""

    def __init__(self, card, target):
        super().__init__()
        self.card = card
        self.target = target
        self.setObjectName("card")
        self.setStyleSheet("QFrame#card { background: #171a21;"
                           " border: 1px solid #262b36; border-radius: 8px; }")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(8)

        self.led = QLabel("\u25CF")
        self.led.setFixedWidth(14)
        head.addWidget(self.led)

        self.chk_on = QCheckBox()
        self.chk_on.setChecked(bool(target.get("enabled")))
        self.chk_on.setToolTip("Off skips this program without deleting it")
        self.chk_on.toggled.connect(
            lambda on: self.store("enabled", bool(on)))
        head.addWidget(self.chk_on)

        self.name = QLineEdit(target.name)
        self.name.setMaxLength(40)
        self.name.setFixedWidth(150)
        self.name.setToolTip("Only a label for this panel")
        self.name.editingFinished.connect(self.on_name)
        head.addWidget(self.name)

        self.kind = QComboBox()
        self.kind.setFixedWidth(160)
        for value, label in KINDS:
            self.kind.addItem(label, value)
        index = self.kind.findData(target.kind)
        self.kind.setCurrentIndex(index if index >= 0 else 0)
        self.kind.setToolTip(
            "Program / file: pick it with the file dialog.\n"
            "Command: type it exactly as a terminal would take it, pipes "
            "and && included.\n"
            "OSCLeash plugin: start and stop the leashes through the "
            "OSCLeash plugin itself instead of as a second process.")
        self.kind.currentIndexChanged.connect(self.on_kind)
        head.addWidget(self.kind)

        self.state = _small("")
        head.addWidget(self.state, 1)

        self.btn_run = _button("\u25B6  Start", _btn_style("#25332b", GREEN,
                                                          "#d6f0df"),
                               "Start or stop just this program", 28)
        self.btn_run.clicked.connect(self.on_run)
        head.addWidget(self.btn_run)

        self.btn_del = _icon_button("\U0001F5D1", "Remove this program")
        self.btn_del.clicked.connect(self.on_delete)
        head.addWidget(self.btn_del)
        outer.addLayout(head)

        # ---------------------------------------------------- file row
        self.file_row = QWidget()
        frow = QHBoxLayout(self.file_row)
        frow.setContentsMargins(22, 0, 0, 0)
        frow.setSpacing(8)

        self.path = QLineEdit(str(target.get("path") or ""))
        self.path.setToolTip(
            "A .sh, an AppImage, a binary or a .py on Linux; an .exe, .bat, "
            ".cmd, .lnk or .py on Windows. In Command mode this is the "
            "whole command line instead.")
        self.path.editingFinished.connect(
            lambda: self.store("path", self.path.text().strip()))
        frow.addWidget(self.path, 3)

        self.btn_pick = _button("Browse\u2026", _btn_style("#232733", "#333947",
                                                          "#cfd6e2", "#2c3140"),
                                "Pick the file to start", 28)
        self.btn_pick.clicked.connect(self.on_pick)
        frow.addWidget(self.btn_pick)

        self.args = QLineEdit(str(target.get("args") or ""))
        self.args.setPlaceholderText("arguments")
        self.args.setToolTip("Passed to the program, quoted like a shell "
                             "would quote them")
        self.args.editingFinished.connect(
            lambda: self.store("args", self.args.text().strip()))
        frow.addWidget(self.args, 1)
        outer.addWidget(self.file_row)

        # ------------------------------------------------- options row
        self.opt_row = QWidget()
        orow = QHBoxLayout(self.opt_row)
        orow.setContentsMargins(22, 0, 0, 0)
        orow.setSpacing(10)

        orow.addWidget(_small("start after"))
        self.delay = QSpinBox()
        self.delay.setObjectName("smallspin")
        self.delay.setRange(0, 600)
        self.delay.setSuffix(" s")
        self.delay.setFixedWidth(80)
        self.delay.setValue(int(target.get("delay") or 0))
        self.delay.setToolTip(
            "Seconds between the trigger and this program. Gives a set an "
            "order: SteamVR first, the overlay once there is something to "
            "overlay onto.")
        self.delay.valueChanged.connect(lambda v: self.store("delay", int(v)))
        orow.addWidget(self.delay)

        self.chk_stop = QCheckBox("stop again")
        self.chk_stop.setChecked(bool(target.get("stop_with")))
        self.chk_stop.setToolTip(
            "Stop this program again once the trigger is gone. Off leaves "
            "it running – for something you want up for the rest of the "
            "session.")
        self.chk_stop.toggled.connect(
            lambda on: self.store("stop_with", bool(on)))
        orow.addWidget(self.chk_stop)

        self.chk_skip = QCheckBox("skip if already running")
        self.chk_skip.setChecked(bool(target.get("skip_if_running")))
        self.chk_skip.setToolTip(
            "Do not start a second copy when the program is already up – "
            "including one you started by hand.")
        self.chk_skip.toggled.connect(
            lambda on: self.store("skip_if_running", bool(on)))
        orow.addWidget(self.chk_skip)

        self.stop_cmd = QLineEdit(str(target.get("stop_cmd") or ""))
        self.stop_cmd.setPlaceholderText("stop command (optional)")
        self.stop_cmd.setToolTip(
            "Run this instead of killing the process – for a program that "
            "wants to be shut down its own way. Empty is the normal case.")
        self.stop_cmd.editingFinished.connect(
            lambda: self.store("stop_cmd", self.stop_cmd.text().strip()))
        orow.addWidget(self.stop_cmd, 1)
        outer.addWidget(self.opt_row)

        self.note = _small("")
        self.note.setWordWrap(True)
        self.note.setVisible(False)
        outer.addWidget(self.note)

        self.apply_kind()

    # ------------------------------------------------------------ data
    def store(self, key, value):
        self.target.set(key, value)
        self.card.save()

    def on_name(self):
        self.store("name", self.name.text().strip() or "Program")

    def on_kind(self, _index):
        self.store("kind", self.kind.currentData() or "path")
        self.apply_kind()

    def apply_kind(self):
        kind = self.target.kind
        is_leash = kind == "oscleash"
        self.file_row.setVisible(not is_leash)
        self.btn_pick.setVisible(kind == "path")
        self.args.setVisible(kind == "path")
        self.stop_cmd.setVisible(not is_leash)
        self.chk_skip.setVisible(not is_leash)
        if kind == "command":
            self.path.setPlaceholderText(
                "e.g. wlx-overlay-s --show   or   flatpak run com.example.App")
        else:
            self.path.setPlaceholderText("path to the program")
        self.note.setVisible(is_leash)
        if is_leash:
            self.note.setText(
                "Starts and stops every leash configured in the OSCLeash "
                "plugin, through that plugin's own manager – so the debug "
                "consoles, the generated configs and the crash watchdog "
                "keep working. The OSCLeash plugin has to be installed and "
                "switched on.")

    # --------------------------------------------------------- actions
    def on_pick(self):
        start = str(self.target.get("path") or "") or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, "Pick a program", start, FILE_FILTER)
        if path:
            self.path.setText(path)
            self.store("path", path)
            if self.name.text().strip() in ("", "Program") or \
                    self.name.text().startswith("Program "):
                nice = Path(path).stem
                self.name.setText(nice)
                self.store("name", nice)

    def on_run(self):
        if self.target.running:
            self.target.stop()
        else:
            err = self.target.start(force=True)
            if err:
                self.card.panel.show_error(err)
        self.card.panel.sync()

    def on_delete(self):
        self.card.remove_target(self)

    # ----------------------------------------------------------- state
    def sync(self):
        running = self.target.running
        pending = bool(self.target.due_at)
        colour = GREEN if running else (AMBER if pending else GREY)
        if not running and self.target.error:
            colour = RED
        self.led.setStyleSheet(f"color: {colour};")
        self.state.setText(self.target.state_text())
        self.btn_run.setText("\u25A0  Stop" if running else "\u25B6  Start")
        self.btn_run.setStyleSheet(
            _btn_style("#3a2a2c", RED, "#f0d6d4") if running
            else _btn_style("#25332b", GREEN, "#d6f0df"))
        self.btn_run.setEnabled(self.target.kind != "oscleash"
                                or self.target.link is None
                                or self.target.link.available())


class RuleCard(QFrame):
    """One rule: what has to run, and what starts because of it."""

    def __init__(self, panel, rule, expanded=False):
        super().__init__()
        self.panel = panel
        self.rule = rule
        self.trigger_rows = []
        self.target_rows = []
        self.setObjectName("card")
        self.setStyleSheet("QFrame#card { background: #191c24;"
                           " border: 1px solid #2a2f3a; border-radius: 10px; }")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)

        self.led = QLabel("\u25CF")
        self.led.setFixedWidth(16)
        head.addWidget(self.led)

        self.chk_on = QCheckBox()
        self.chk_on.setChecked(bool(rule.get("enabled")))
        self.chk_on.setToolTip("Off ignores this rule completely")
        self.chk_on.toggled.connect(self.on_enabled)
        head.addWidget(self.chk_on)

        self.name = QLineEdit(rule.name)
        self.name.setMaxLength(40)
        self.name.setFixedWidth(170)
        self.name.setToolTip("Only a label – it is what "
                             "{vr_autostart_rule} says")
        self.name.editingFinished.connect(self.on_name)
        head.addWidget(self.name)

        self.state = _small("")
        head.addWidget(self.state, 1)

        self.btn_run = _button("\u25B6  Run now",
                               _btn_style("#25332b", GREEN, "#d6f0df"),
                               "Start this rule's programs without waiting "
                               "for the trigger", 30)
        self.btn_run.clicked.connect(self.on_run)
        head.addWidget(self.btn_run)

        self.btn_more = _icon_button("\u2699", "Triggers and programs")
        self.btn_more.setCheckable(True)
        self.btn_more.setChecked(expanded)
        self.btn_more.toggled.connect(self.on_toggle)
        head.addWidget(self.btn_more)

        self.btn_del = _icon_button("\U0001F5D1", "Delete this rule")
        self.btn_del.clicked.connect(self.on_delete)
        head.addWidget(self.btn_del)
        outer.addLayout(head)

        # ------------------------------------------------------- body
        self.body = QWidget()
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 4, 0, 0)
        body.setSpacing(8)

        trig_head = QHBoxLayout()
        trig_head.setSpacing(8)
        title = QLabel("Start when this runs")
        title.setObjectName("cardtitle")
        trig_head.addWidget(title)

        self.match = QComboBox()
        self.match.addItem("any of them is enough", "any")
        self.match.addItem("all of them at the same time", "all")
        self.match.setFixedWidth(220)
        index = self.match.findData(rule.get("match"))
        self.match.setCurrentIndex(index if index >= 0 else 0)
        self.match.setToolTip(
            "\u201call of them\u201d is the one people usually want for a "
            "pair: SteamVR *and* VRChat, so an overlay does not come up "
            "while only the runtime is warming.")
        self.match.currentIndexChanged.connect(self.on_match)
        trig_head.addWidget(self.match)
        trig_head.addStretch()

        self.btn_trig = _button("\uFF0B  Trigger",
                                _btn_style("#2b3a4d", BLUE, "#cfe0f5"),
                                "One more program that has to be running", 28)
        self.btn_trig.clicked.connect(self.on_add_trigger)
        trig_head.addWidget(self.btn_trig)
        body.addLayout(trig_head)

        self.trig_box = QVBoxLayout()
        self.trig_box.setSpacing(4)
        body.addLayout(self.trig_box)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setObjectName("hline")
        body.addWidget(line)

        prog_head = QHBoxLayout()
        prog_head.setSpacing(8)
        title2 = QLabel("Then start these")
        title2.setObjectName("cardtitle")
        prog_head.addWidget(title2)
        prog_head.addStretch()

        prog_head.addWidget(_small("stop after"))
        self.grace = QSpinBox()
        self.grace.setObjectName("smallspin")
        self.grace.setRange(0, 300)
        self.grace.setSuffix(" s")
        self.grace.setFixedWidth(80)
        self.grace.setValue(int(rule.get("grace") or 0))
        self.grace.setToolTip(
            "How long the trigger may be gone before the programs are "
            "stopped. A game restarting its own process, a headset "
            "reconnect and a world crash all look like \u201cquit\u201d for "
            "a moment – this is the difference between a hiccup and losing "
            "the overlay you are standing in.")
        self.grace.valueChanged.connect(
            lambda v: self.store("grace", int(v)))
        prog_head.addWidget(self.grace)

        self.btn_prog = _button("\uFF0B  Program",
                                _btn_style("#2b3a4d", BLUE, "#cfe0f5"),
                                "One more program to start with the "
                                "trigger", 28)
        self.btn_prog.clicked.connect(self.on_add_target)
        prog_head.addWidget(self.btn_prog)
        body.addLayout(prog_head)

        self.prog_box = QVBoxLayout()
        self.prog_box.setSpacing(6)
        body.addLayout(self.prog_box)

        outer.addWidget(self.body)
        self.body.setVisible(expanded)

        for data in self.rule.triggers:
            self._add_trigger_row(data)
        for target in self.rule.targets:
            self._add_target_row(target)

    # ------------------------------------------------------------ data
    def save(self):
        self.panel.engine.save()

    def store(self, key, value):
        self.rule.set(key, value)
        self.save()

    def on_name(self):
        self.store("name", self.name.text().strip() or "Rule")

    def on_enabled(self, on):
        self.store("enabled", bool(on))

    def on_match(self, _index):
        self.store("match", self.match.currentData() or "any")

    def on_toggle(self, on):
        self.body.setVisible(bool(on))

    # ------------------------------------------------------------ rows
    def _add_trigger_row(self, data):
        row = TriggerRow(self, data)
        self.trigger_rows.append(row)
        self.trig_box.addWidget(row)
        return row

    def on_add_trigger(self):
        self._add_trigger_row(self.rule.add_trigger())
        self.save()
        if not self.btn_more.isChecked():
            self.btn_more.setChecked(True)

    def remove_trigger(self, row):
        self.rule.remove_trigger(row.data.get("id"))
        self.trigger_rows.remove(row)
        self.trig_box.removeWidget(row)
        row.deleteLater()
        self.save()

    def _add_target_row(self, target):
        row = TargetRow(self, target)
        self.target_rows.append(row)
        self.prog_box.addWidget(row)
        return row

    def on_add_target(self):
        self._add_target_row(self.rule.add_target())
        self.save()
        if not self.btn_more.isChecked():
            self.btn_more.setChecked(True)

    def remove_target(self, row):
        name = row.target.name
        if row.target.running:
            answer = QMessageBox.question(
                self, "Remove program",
                f"\u201c{name}\u201d is running. Stop it and remove it?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.rule.remove_target(row.target.tid)
        self.target_rows.remove(row)
        self.prog_box.removeWidget(row)
        row.deleteLater()
        self.save()

    # --------------------------------------------------------- actions
    def on_run(self):
        engine = self.panel.engine
        if self.rule.running_targets():
            engine.stop_rule(self.rule.rid)
        else:
            engine.run_rule(self.rule.rid)
        self.panel.sync()

    def on_delete(self):
        answer = QMessageBox.question(
            self, "Delete rule",
            f"Delete \u201c{self.rule.name}\u201d?\nIts programs are stopped "
            f"first. Nothing is uninstalled – only this rule goes away.")
        if answer == QMessageBox.StandardButton.Yes:
            self.panel.remove_card(self)

    # ----------------------------------------------------------- state
    def sync(self, snap, ignore):
        for row in self.trigger_rows:
            row.sync(snap, ignore)
        for row in self.target_rows:
            row.sync()

        running = self.rule.running_targets()
        colour = GREY
        if not self.rule.get("enabled"):
            colour = GREY
        elif self.rule.state == "active":
            colour = GREEN if running else AMBER
        elif self.rule.state == "losing":
            colour = AMBER
        elif self.panel.engine.armed:
            colour = BLUE
        self.led.setStyleSheet(f"color: {colour};")
        self.state.setText(self.rule.state_text())
        self.btn_run.setText("\u25A0  Stop" if running else "\u25B6  Run now")
        self.btn_run.setStyleSheet(
            _btn_style("#3a2a2c", RED, "#f0d6d4") if running
            else _btn_style("#25332b", GREEN, "#d6f0df"))


class AutostartPanel(QWidget):
    """The two big buttons, and everything they act on."""

    BIG = ("QPushButton {{ background: {bg}; border: 2px solid {edge};"
           " border-radius: 12px; color: {fg}; font-size: 15px;"
           " font-weight: 600; }}"
           "QPushButton:hover {{ background: {edge}; }}"
           "QPushButton:disabled {{ background: #23262f;"
           " border-color: #333947; color: #666d7a; }}")

    def __init__(self, api, engine, parent=None):
        super().__init__(parent)
        self.api = api
        self.engine = engine
        self.cards = []
        self.log_win = None

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(10)

        big = QHBoxLayout()
        big.setSpacing(10)
        self.btn_start = QPushButton("\u25B6   START AUTOSTART")
        self.btn_stop = QPushButton("\u25A0   STOP AUTOSTART")
        for btn, tip in ((self.btn_start,
                          "Watch the triggers. Anything already running is "
                          "left alone."),
                         (self.btn_stop,
                          "Stop watching and stop everything this plugin "
                          "started – in one press.")):
            btn.setMinimumHeight(56)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(tip)
            big.addWidget(btn, 1)
        self.btn_start.clicked.connect(self.on_start)
        self.btn_stop.clicked.connect(self.on_stop)
        box.addLayout(big)

        # what the triggers see, before anyone builds a rule around it
        self.strip = RuntimeStrip(self)
        box.addWidget(self.strip)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.btn_add = _button("\uFF0B  Add rule",
                               _btn_style("#2b3a4d", BLUE, "#cfe0f5"),
                               "One more \u201cwhen this runs, start "
                               "those\u201d", 32)
        self.btn_add.clicked.connect(self.on_add)
        bar.addWidget(self.btn_add)

        self.btn_log = _button("\U0001F4C4  Log",
                               _btn_style("#232733", "#333947", "#cfd6e2",
                                          "#2c3140"),
                               "What the watcher did, and when", 32)
        self.btn_log.clicked.connect(self.on_log)
        bar.addWidget(self.btn_log)

        self.btn_recheck = _button("\u21BB  Check again",
                                   _btn_style("#232733", "#333947", "#cfd6e2",
                                              "#2c3140"),
                                   "Ask again right now – after starting or "
                                   "installing something outside the app", 32)
        self.btn_recheck.clicked.connect(self.on_recheck)
        bar.addWidget(self.btn_recheck)

        bar.addStretch()
        self.info = _small("")
        bar.addWidget(self.info)
        box.addLayout(bar)

        self.warn = QLabel("")
        self.warn.setWordWrap(True)
        self.warn.setStyleSheet(f"color: {AMBER};")
        self.warn.setVisible(False)
        box.addWidget(self.warn)

        self.list_box = QVBoxLayout()
        self.list_box.setSpacing(10)
        box.addLayout(self.list_box)
        box.addStretch()

        for index, rule in enumerate(self.engine.rules):
            self._add_card(rule, expanded=(index == 0 and len(
                self.engine.rules) == 1))

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.sync)
        self.timer.start(POLL_MS)
        self.sync()

    # ------------------------------------------------------------ rows
    def _add_card(self, rule, expanded=False):
        card = RuleCard(self, rule, expanded)
        self.cards.append(card)
        self.list_box.addWidget(card)
        return card

    def on_add(self):
        self._add_card(self.engine.add_rule(), expanded=True)
        self.sync()

    def remove_card(self, card):
        self.engine.remove_rule(card.rule.rid)
        self.cards.remove(card)
        self.list_box.removeWidget(card)
        card.deleteLater()
        if not self.engine.rules:
            # the engine always keeps one rule around, so the panel is
            # never an empty box with no way back
            self._add_card(self.engine.add_rule("Rule"), expanded=True)
        self.sync()

    # --------------------------------------------------------- actions
    def on_start(self):
        self.engine.arm()
        self.engine.tick(force=True)
        self.sync()

    def on_stop(self):
        self.engine.disarm(stop_targets=True)
        self.sync()

    def on_recheck(self):
        # the process snapshot is cached and so are the systemd answers;
        # this is the button for "I just started it, look again"
        procs.forget_probes()
        procs.snapshot(force=True)
        self.engine.tick(force=True)
        self.sync()

    def on_log(self):
        if self.log_win is None:
            self.log_win = EventWindow(self.engine, self.window())
            self.log_win.finished.connect(self._forget_log)
        self.log_win.show()
        self.log_win.raise_()
        self.log_win.activateWindow()

    def _forget_log(self, *_):
        self.log_win = None

    def show_error(self, text):
        QMessageBox.warning(self, "VR Autostart", text)

    # ----------------------------------------------------------- state
    def sync(self):
        snap = procs.snapshot()
        ignore = self.engine.own_pids()
        self.strip.sync(snap, ignore)
        for card in self.cards:
            card.sync(snap, ignore)

        armed = self.engine.armed
        running = self.engine.running_count()
        self.btn_start.setEnabled(not armed)
        self.btn_start.setText("\u25B6   AUTOSTART RUNNING" if armed
                               else "\u25B6   START AUTOSTART")
        self.btn_start.setStyleSheet(self.BIG.format(
            bg="#25332b" if not armed else "#1d2a22",
            edge=GREEN, fg="#d6f0df"))
        self.btn_stop.setEnabled(armed or bool(running))
        self.btn_stop.setStyleSheet(self.BIG.format(
            bg="#3a2a2c", edge=RED, fg="#f0d6d4"))

        total = self.engine.target_count()
        state = "armed" if armed else "stopped"
        self.info.setText(f"{state} \u00b7 {running}/{total} programs "
                          f"running \u00b7 {procs.backend_name()}")

        problems = []
        if not armed and running:
            problems.append("The watcher is off, but programs it started are "
                            "still running – Stop takes them down.")
        for rule in self.engine.rules:
            if not rule.get("enabled"):
                continue
            if not rule.patterns():
                problems.append(f"\u201c{rule.name}\u201d has no trigger, so "
                                f"it only runs when you press Run now.")
            for target in rule.targets:
                if target.kind == "oscleash" and target.link is not None \
                        and not target.link.available():
                    problems.append(
                        "The OSCLeash plugin is not loaded – switch it on in "
                        "the plugin list, or this program does nothing.")
                    break
        self.warn.setText("\n".join(problems))
        self.warn.setVisible(bool(problems))


class PanelWindow(QDialog):
    """Standalone shell around the panel, for a host that cannot embed a
    plugin widget itself."""

    def __init__(self, api, engine, parent=None):
        super().__init__(parent)
        self.setWindowTitle("VR Autostart")
        self.resize(880, 620)
        self.setStyleSheet("QDialog { background: #14161c; }"
                           "QLabel { color: #b9c2d0; }")
        box = QVBoxLayout(self)
        box.setContentsMargins(16, 16, 16, 16)
        title = QLabel("VR Autostart")
        title.setObjectName("cardtitle")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        box.addWidget(title)
        self.panel = AutostartPanel(api, engine, self)
        box.addWidget(self.panel, 1)
