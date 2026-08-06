"""The Qt side of the OSCLeash plugin.

Three widgets:

    OSCLeashPanel   the whole thing – binary status, Start/Stop all, + and
                    one row per configured leash
    InstanceRow     one leash: name, LED, Start/Stop, Debug, settings
    DebugWindow     the log of exactly one instance, in its own window

Everything reads from :mod:`runner` through a single 400 ms timer rather
than through signals from the reader threads. The chatbox learned that
lesson once already: a Qt widget touched from a non-GUI thread is a
SIGSEGV, and a poll on the GUI thread cannot be one.

The object names (card, cardtitle, dim, iconbtn, smallspin, hline) are
the app's own, so the panel inherits the stylesheet instead of inventing
a second look.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QGuiApplication
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from .runtime import describe, forget_probes, port_free, preflight

POLL_MS = 400
LOG_MS = 250

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


def _btn_style(bg, edge, fg="#e6ecf5", hover=None):
    return BTN.format(bg=bg, edge=edge, fg=fg, hover=hover or edge)


class DebugWindow(QDialog):
    """One instance's console. Non-modal on purpose – people watch this
    while they are in the headset getting dragged around."""

    def __init__(self, inst, parent=None):
        super().__init__(parent)
        self.inst = inst
        self.setWindowTitle(f"OSCLeash debug – {inst.name}")
        self.resize(720, 420)
        self.setStyleSheet("QDialog { background: #14161c; }"
                           "QLabel { color: #b9c2d0; }")

        box = QVBoxLayout(self)
        box.setContentsMargins(12, 12, 12, 12)
        box.setSpacing(8)

        head = QHBoxLayout()
        self.status = QLabel("")
        self.status.setObjectName("dim")
        head.addWidget(self.status)
        head.addStretch()
        self.chk_follow = QCheckBox("Auto scroll")
        self.chk_follow.setChecked(True)
        head.addWidget(self.chk_follow)
        for label, slot in (("Copy", self.copy_all), ("Clear", self.clear_log)):
            b = QPushButton(label)
            b.setFixedHeight(28)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(_btn_style("#232733", "#333947", "#cfd6e2",
                                       "#2c3140"))
            b.clicked.connect(slot)
            head.addWidget(b)
        box.addLayout(head)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(1000)
        self.view.setFont(QFont("monospace", 10))
        self.view.setStyleSheet(
            "QPlainTextEdit { background: #0f1116; color: #c8d2e0;"
            " border: 1px solid #333947; border-radius: 8px; padding: 6px; }")
        box.addWidget(self.view, 1)

        note = QLabel("This is the raw output of this OSCLeash process. "
                      "Direction values only appear while “Debug output” is "
                      "on in the instance settings.")
        note.setObjectName("dim")
        note.setWordWrap(True)
        box.addWidget(note)

        self._shown = ""
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(LOG_MS)
        self.refresh()

    def refresh(self):
        text = self.inst.log_text()
        if text != self._shown:
            self._shown = text
            bar = self.view.verticalScrollBar()
            at_end = bar.value() >= bar.maximum() - 4
            self.view.setPlainText(text)
            if self.chk_follow.isChecked() or at_end:
                self.view.verticalScrollBar().setValue(
                    self.view.verticalScrollBar().maximum())
        if self.inst.running:
            pid = self.inst.proc.pid if self.inst.proc else "?"
            ready, active, direction, mag = self.inst.state()
            extra = f" – pulled {direction} ({mag:.2f})" if active else \
                    (" – awaiting input" if ready else "")
            self.status.setText(f"running, pid {pid}{extra}")
        elif self.inst.exit_code is not None:
            self.status.setText(f"stopped (exit code {self.inst.exit_code})")
        else:
            self.status.setText("not running")

    def copy_all(self):
        QGuiApplication.clipboard().setText(self.inst.log_text())

    def clear_log(self):
        self.inst.clear_log()
        self._shown = ""
        self.view.clear()

    def closeEvent(self, event):
        self.timer.stop()
        super().closeEvent(event)


class InstanceRow(QFrame):
    """Header row plus a collapsible settings block for one leash."""

    def __init__(self, panel, inst):
        super().__init__()
        self.panel = panel
        self.inst = inst
        self.debug_win = None
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

        self.name = QLineEdit(inst.name)
        self.name.setMaxLength(32)
        self.name.setFixedWidth(150)
        self.name.setToolTip("Only a label for this panel and the "
                             "{oscleash_name} placeholder")
        self.name.editingFinished.connect(self.on_name)
        head.addWidget(self.name)

        self.state = QLabel("")
        self.state.setObjectName("dim")
        head.addWidget(self.state, 1)

        self.btn_run = QPushButton("\u25B6  Start")
        self.btn_run.setFixedHeight(30)
        self.btn_run.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_run.clicked.connect(self.on_run)
        head.addWidget(self.btn_run)

        self.btn_debug = QPushButton("\U0001F41E  Debug")
        self.btn_debug.setFixedHeight(30)
        self.btn_debug.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_debug.setStyleSheet(_btn_style("#232733", "#333947",
                                                "#cfd6e2", "#2c3140"))
        self.btn_debug.setToolTip("Open the console of this instance")
        self.btn_debug.clicked.connect(self.on_debug)
        head.addWidget(self.btn_debug)

        self.btn_more = QPushButton("\u2699")
        self.btn_more.setObjectName("iconbtn")
        self.btn_more.setFixedSize(30, 30)
        self.btn_more.setCheckable(True)
        self.btn_more.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_more.setToolTip("Settings of this leash")
        head.addWidget(self.btn_more)

        self.btn_del = QPushButton("\U0001F5D1")
        self.btn_del.setObjectName("iconbtn")
        self.btn_del.setFixedSize(30, 30)
        self.btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_del.setToolTip("Remove this leash")
        self.btn_del.clicked.connect(self.on_delete)
        head.addWidget(self.btn_del)

        outer.addLayout(head)

        self.body = self._build_body()
        self.body.setVisible(False)
        self.btn_more.toggled.connect(self.body.setVisible)
        outer.addWidget(self.body)
        self.sync()

    # ----------------------------------------------------------- body
    def _build_body(self):
        body = QWidget()
        grid = QGridLayout(body)
        grid.setContentsMargins(4, 6, 4, 2)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        row = 0

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setObjectName("hline")
        grid.addWidget(line, row, 0, 1, 4)
        row += 1

        self.ed_bones = self._text(
            grid, row, "Physbone parameter(s)", "physbones",
            "The parameter names of the leash physbones, comma separated. "
            "OSCLeash listens for <name>_IsGrabbed and <name>_Stretch.\n"
            "A turning leash keeps its direction suffix, e.g. Leash_North.")
        row += 1
        self.ed_prefix = self._text(
            grid, row, "Contact prefix", "prefix",
            "Stem of the six directional contacts (<prefix>_Z+, _Z-, _X+ …).\n"
            "Empty = taken from the first physbone above, which is what the "
            "stock prefab does.")
        self.ed_prefix.setPlaceholderText("auto")
        row += 1

        ports = QWidget()
        pbox = QHBoxLayout(ports)
        pbox.setContentsMargins(0, 0, 0, 0)
        pbox.setSpacing(8)
        self.sp_listen = self._spin(9000, 65535, "listen_port")
        self.sp_send = self._spin(9000, 65535, "send_port")
        pbox.addWidget(QLabel("listen"))
        pbox.addWidget(self.sp_listen)
        pbox.addWidget(QLabel("send"))
        pbox.addWidget(self.sp_send)
        self.chk_query = self._check(
            "OSCQuery", "oscquery",
            "VRChat only ever sends to port 9001 once. Every additional "
            "instance needs OSCQuery to receive avatar data at all – so the "
            "second leash you add gets it switched on automatically.\n\n"
            "With OSCQuery on, the port above is ignored: OSCLeash asks the "
            "system for a free one and announces it over mDNS, so it cannot "
            "collide with anything. That announcement is why starting takes "
            "a few seconds longer.")
        pbox.addWidget(self.chk_query)
        pbox.addStretch()
        grid.addWidget(QLabel("Ports"), row, 0)
        grid.addWidget(ports, row, 1, 1, 3)
        row += 1

        self._pct(grid, row, "Walk deadzone", "walk_dz", 1, 100,
                  "Minimum stretch before you start walking")
        self._pct(grid, row, "Run deadzone", "run_dz", 1, 100,
                  "Minimum stretch before you start running", col=2)
        row += 1
        self._pct(grid, row, "Strength", "strength", 10, 300,
                  "Speed multiplier – VRChat caps the result at 100 %")
        self._pct(grid, row, "Up/down deadzone", "updown_dz", 1, 100,
                  "Stops movement when the leash is pulled steeply up or "
                  "down. 100 % disables it.", col=2)
        row += 1
        self._pct(grid, row, "Up/down compensation", "updown_comp", 0, 100,
                  "How much of the vertical angle is compensated")
        row += 1

        self.chk_turn = self._check(
            "Turning", "turning",
            "Turns you toward the pull. Motion sickness warning – and the "
            "physbone parameter needs a direction suffix (Leash_North).")
        grid.addWidget(self.chk_turn, row, 0, 1, 2)
        row += 1
        self._pct(grid, row, "Turning strength", "turn_mult", 1, 200, "")
        self._pct(grid, row, "Turning deadzone", "turn_dz", 1, 100, "", col=2)
        row += 1
        self._int(grid, row, "Turning goal", "turn_goal", 0, 144,
                  "Target angle in degrees (0–144)", suffix="\u00B0")
        row += 1

        self._int(grid, row, "Active delay", "active_delay", 5, 500,
                  "Milliseconds between OSC messages while being pulled",
                  suffix=" ms")
        self._int(grid, row, "Idle delay", "inactive_delay", 50, 5000,
                  "Milliseconds between checks while idle", suffix=" ms",
                  col=2)
        row += 1

        flags = QWidget()
        fbox = QHBoxLayout(flags)
        fbox.setContentsMargins(0, 0, 0, 0)
        self.chk_log = self._check(
            "Debug output", "logging",
            "OSCLeash prints the direction values instead of clearing the "
            "console every tick. Feeds the Debug window and the direction "
            "placeholders – costs a fraction of a percent of CPU.")
        self.chk_auto = self._check(
            "Start with the app", "autostart",
            "Starts this instance when the chatbox starts, provided the "
            "plugin's autostart setting is on.")
        fbox.addWidget(self.chk_log)
        fbox.addWidget(self.chk_auto)
        fbox.addStretch()
        grid.addWidget(flags, row, 0, 1, 4)
        row += 1

        self.hint = QLabel("")
        self.hint.setObjectName("dim")
        self.hint.setWordWrap(True)
        grid.addWidget(self.hint, row, 0, 1, 4)
        return body

    # --------------------------------------------------- field helpers
    def _store(self, key, value):
        self.inst.set(key, value)
        self.panel.manager.save()
        if self.inst.running:
            self.hint.setText("Changed while running – press \u25A0 Stop and "
                              "\u25B6 Start to apply.")

    def _text(self, grid, row, label, key, tip):
        lbl = QLabel(label)
        lbl.setToolTip(tip)
        edit = QLineEdit(str(self.inst.get(key) or ""))
        edit.setMaxLength(120)
        edit.setToolTip(tip)
        edit.editingFinished.connect(
            lambda k=key, e=edit: self._store(k, e.text().strip()))
        grid.addWidget(lbl, row, 0)
        grid.addWidget(edit, row, 1, 1, 3)
        return edit

    def _spin(self, low, high, key, suffix=""):
        w = QSpinBox()
        w.setObjectName("smallspin")
        w.setRange(low, high)
        w.setFixedHeight(28)
        w.setMinimumWidth(84)
        if suffix:
            w.setSuffix(suffix)
        try:
            w.setValue(int(self.inst.get(key)))
        except (TypeError, ValueError):
            pass
        w.valueChanged.connect(lambda v, k=key: self._store(k, int(v)))
        return w

    def _int(self, grid, row, label, key, low, high, tip, suffix="", col=0):
        lbl = QLabel(label)
        if tip:
            lbl.setToolTip(tip)
        w = self._spin(low, high, key, suffix)
        grid.addWidget(lbl, row, col)
        grid.addWidget(w, row, col + 1)
        return w

    def _pct(self, grid, row, label, key, low, high, tip, col=0):
        return self._int(grid, row, label, key, low, high, tip,
                         suffix=" %", col=col)

    def _check(self, label, key, tip):
        w = QCheckBox(label)
        w.setChecked(bool(self.inst.get(key)))
        w.setToolTip(tip)
        w.toggled.connect(lambda on, k=key: self._store(k, bool(on)))
        return w

    # --------------------------------------------------------- actions
    def on_name(self):
        self.inst.set("name", self.name.text().strip() or "Leash")
        self.panel.manager.save()
        if self.debug_win is not None:
            self.debug_win.setWindowTitle(
                f"OSCLeash debug – {self.inst.name}")

    def on_run(self):
        self.hint.setText("")
        if self.inst.running:
            self.panel.manager.stop(self.inst.iid)
        else:
            err = self.panel.manager.start(self.inst.iid)
            if err:
                self.panel.show_error(err)
        self.panel.sync()

    def on_debug(self):
        if self.debug_win is None:
            self.debug_win = DebugWindow(self.inst, self.window())
            self.debug_win.finished.connect(self._forget_debug)
        self.debug_win.show()
        self.debug_win.raise_()
        self.debug_win.activateWindow()

    def _forget_debug(self, *_):
        self.debug_win = None

    def on_delete(self):
        answer = QMessageBox.question(
            self, "Remove leash",
            f"Remove “{self.inst.name}”?\nIts generated config is deleted "
            f"with it – the OSCLeash install itself stays untouched.")
        if answer == QMessageBox.StandardButton.Yes:
            self.panel.remove_row(self)

    # ----------------------------------------------------------- state
    def sync(self):
        running = self.inst.running
        ready, active, direction, mag = self.inst.state(
            self.panel.idle_secs())
        colour = GREY
        if running:
            colour = GREEN if ready else AMBER
        self.led.setStyleSheet(f"color: {colour};")

        if running and active:
            self.state.setText(f"pulled {direction}  \u00B7  {mag:.2f}")
        elif running and ready:
            self.state.setText("awaiting input")
        elif running:
            self.state.setText("starting \u2026")
        elif self.inst.exit_code not in (None, 0):
            self.state.setText(f"exited ({self.inst.exit_code})")
        else:
            self.state.setText("stopped")

        self.btn_run.setText("\u25A0  Stop" if running else "\u25B6  Start")
        self.btn_run.setStyleSheet(
            _btn_style("#3a2a2c", RED, "#f0d6d4") if running
            else _btn_style("#25332b", GREEN, "#d6f0df"))
        if self.debug_win is not None and self.debug_win.isVisible():
            self.btn_debug.setStyleSheet(_btn_style("#2b3a4d", BLUE, "#cfe0f5"))
        else:
            self.btn_debug.setStyleSheet(_btn_style("#232733", "#333947",
                                                    "#cfd6e2", "#2c3140"))


class OSCLeashPanel(QWidget):
    """Everything the user sees: header, buttons, one row per leash."""

    def __init__(self, api, manager, parent=None):
        super().__init__(parent)
        self.api = api
        self.manager = manager
        self.rows = []

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(8)

        self.btn_all = QPushButton("\u25B6  Start all")
        self.btn_all.setFixedHeight(32)
        self.btn_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_all.setStyleSheet(_btn_style("#25332b", GREEN, "#d6f0df"))
        self.btn_all.clicked.connect(self.on_all)
        bar.addWidget(self.btn_all)

        self.btn_add = QPushButton("\uFF0B  Add leash")
        self.btn_add.setFixedHeight(32)
        self.btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_add.setStyleSheet(_btn_style("#2b3a4d", BLUE, "#cfe0f5"))
        self.btn_add.setToolTip(
            "A second leash with its own compass needs its own OSCLeash "
            "process – one config holds exactly one set of contacts.")
        self.btn_add.clicked.connect(self.on_add)
        bar.addWidget(self.btn_add)

        self.btn_recheck = QPushButton("\u21BB  Check again")
        self.btn_recheck.setFixedHeight(32)
        self.btn_recheck.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_recheck.setStyleSheet(_btn_style("#232733", "#333947",
                                                  "#cfd6e2", "#2c3140"))
        self.btn_recheck.setToolTip(
            "Look for python and the optional modules again - after "
            "installing something outside the app")
        self.btn_recheck.clicked.connect(self.on_recheck)
        self.btn_recheck.setVisible(False)
        bar.addWidget(self.btn_recheck)

        bar.addStretch()
        self.info = QLabel("")
        self.info.setObjectName("dim")
        bar.addWidget(self.info)
        box.addLayout(bar)

        self.warn = QLabel("")
        self.warn.setWordWrap(True)
        self.warn.setStyleSheet(f"color: {AMBER};")
        self.warn.setVisible(False)
        box.addWidget(self.warn)

        self.list_box = QVBoxLayout()
        self.list_box.setSpacing(8)
        box.addLayout(self.list_box)
        box.addStretch()

        for inst in self.manager.instances:
            self._add_row(inst)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.sync)
        self.timer.start(POLL_MS)
        self.sync()

    # ------------------------------------------------------------ rows
    def _add_row(self, inst):
        row = InstanceRow(self, inst)
        self.rows.append(row)
        self.list_box.addWidget(row)
        return row

    def on_add(self):
        self._add_row(self.manager.add())
        self.sync()

    def remove_row(self, row):
        if len(self.rows) <= 1:
            self.show_error("At least one leash has to stay – clear its "
                            "settings instead.")
            return
        self.manager.remove(row.inst.iid)
        self.rows.remove(row)
        self.list_box.removeWidget(row)
        if row.debug_win is not None:
            row.debug_win.close()
        row.deleteLater()
        self.sync()

    def on_recheck(self):
        forget_probes()
        self.sync()

    def on_all(self):
        if self.manager.running_count():
            self.manager.stop_all()
        else:
            self.manager.start_all()
        self.sync()

    # ---------------------------------------------------------- helpers
    def idle_secs(self):
        try:
            return max(1, int(self.api.get("idle_secs", 3)))
        except (TypeError, ValueError):
            return 3

    def show_error(self, text):
        QMessageBox.warning(self, "OSCLeash", text)

    def sync(self):
        for row in self.rows:
            row.sync()

        running = self.manager.running_count()
        total = len(self.rows)
        self.btn_all.setText("\u25A0  Stop all" if running else "\u25B6  Start all")
        self.btn_all.setStyleSheet(
            _btn_style("#3a2a2c", RED, "#f0d6d4") if running
            else _btn_style("#25332b", GREEN, "#d6f0df"))

        override = str(self.api.get("binary", "") or "").strip()
        self.info.setText(f"{running}/{total} running \u00B7 "
                          + (override or describe()))

        problems = []
        # OSCLeash ships inside the plugin, so the only questions left are
        # whether the vendor folder survived the install and whether there
        # is an interpreter to run it with
        wants_query = any(i.get("oscquery") for i in self.manager.instances)
        stopper = preflight(wants_query)
        if stopper:
            problems.append(stopper)
        # a port held by something else, checked only for leashes that are
        # NOT running - a running one is holding its own port, and telling
        # the user their leash is in its own way would be nonsense
        for inst in self.manager.instances:
            if inst.running or inst.get("oscquery"):
                continue
            port = inst.get("listen_port")
            if not port_free(port, str(inst.get("ip") or "127.0.0.1")):
                problems.append(
                    f"Port {port} ({inst.name}) is already in use by "
                    f"something else. Switch OSCQuery on for this leash or "
                    f"pick a free port.")
        for port in self.manager.port_conflicts():
            problems.append(
                f"Two running instances listen on port {port}. Only one of "
                f"them gets the data – switch OSCQuery on for the others.")
        self.warn.setText("\n".join(problems))
        self.warn.setVisible(bool(problems))
        # the fix for these happens outside the app - installing python,
        # installing zeroconf - so the answer has to be re-askable
        self.btn_recheck.setVisible(bool(problems))


class PanelWindow(QDialog):
    """Standalone shell around the panel, used when the plugin page can
    not embed a plugin widget itself."""

    def __init__(self, api, manager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("OSCLeash")
        self.resize(760, 560)
        self.setStyleSheet("QDialog { background: #14161c; }"
                           "QLabel { color: #b9c2d0; }")
        box = QVBoxLayout(self)
        box.setContentsMargins(16, 16, 16, 16)
        title = QLabel("OSCLeash")
        title.setObjectName("cardtitle")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        box.addWidget(title)
        self.panel = OSCLeashPanel(api, manager, self)
        box.addWidget(self.panel, 1)
