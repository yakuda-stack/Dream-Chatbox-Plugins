"""The custom widget half of World Stats – a live view of the battery
backend.

Everything Qt lives in this file and nothing imports it until
build_widget() is called, so the plugin still loads in a headless
manager.

The panel polls the monitor with a QTimer instead of being pushed to
from the worker thread. That is deliberate: polling on the GUI thread
cannot race with anything, while a signal emitted from a worker thread
can reach a widget that is halfway through being deleted.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import threading

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout,
    QWidget)

BTN = ("QPushButton {{ background: {bg}; border: 1px solid {edge};"
       " border-radius: 8px; color: {fg}; padding: 0 14px; }}"
       "QPushButton:hover {{ background: {edge}; }}")


class BatteryPanel(QWidget):
    """One panel per plugin, re-used across page rebuilds."""

    _instance = None

    @classmethod
    def instance(cls, api, get_monitor, parent=None):
        """Hand back the existing panel, or build a fresh one.

        The page rebuilds whenever the plugin list changes, which
        deletes the embedded widget on the C++ side. The python object
        survives that, so touching it raises RuntimeError – which is the
        signal to build a new one rather than a crash."""
        if cls._instance is not None:
            try:
                cls._instance.isVisible()
            except RuntimeError:
                cls._instance = None
        if cls._instance is None:
            cls._instance = cls(api, get_monitor, parent)
        else:
            cls._instance.get_monitor = get_monitor
        return cls._instance

    def __init__(self, api, get_monitor, parent=None):
        super().__init__(parent)
        self.api = api
        self.get_monitor = get_monitor
        self._note = ""

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(8)

        head = QLabel("What the battery backend sees right now. The serial "
                      "in the list is what goes into \u201cadb serial\u201d "
                      "when more than one device is attached.")
        head.setObjectName("dim")
        head.setWordWrap(True)
        box.addWidget(head)

        row = QHBoxLayout()
        row.setSpacing(8)
        for caption, style, slot in (
                ("Read now", ("#2b3a4d", "#5b8dc9", "#cfe0f5"), self.on_read),
                ("List adb devices", ("#232733", "#333947", "#cfd6e2"),
                 self.on_list),
                ("Refresh preview", ("#232733", "#333947", "#cfd6e2"),
                 self.on_refresh)):
            btn = QPushButton(caption)
            btn.setFixedHeight(30)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(BTN.format(bg=style[0], edge=style[1],
                                         fg=style[2]))
            btn.clicked.connect(slot)
            row.addWidget(btn)
        row.addStretch()
        self.state = QLabel("")
        self.state.setObjectName("dim")
        row.addWidget(self.state)
        box.addLayout(row)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setObjectName("hline")
        box.addWidget(line)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(200)
        self.view.setFixedHeight(120)
        self.view.setFont(QFont("monospace", 9))
        self.view.setStyleSheet(
            "QPlainTextEdit { background: #0f1116; color: #c8d2e0;"
            " border: 1px solid #333947; border-radius: 8px; padding: 6px; }")
        box.addWidget(self.view)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.sync)
        self.timer.start(1000)
        self.sync()

    # ---------------------------------------------------------- actions
    def on_read(self):
        mon = self.get_monitor()
        if mon is None:
            self._note = "the battery block is off"
            return
        self._note = "reading \u2026"
        mon.poll_now()

    def on_list(self):
        """adb spawns a process, so it does not happen on this thread."""
        self._note = "asking adb \u2026"
        threading.Thread(target=self._list_worker, daemon=True).start()

    def _list_worker(self):
        try:
            from .battery import adb_devices, find_adb
            try:
                wanted = str(self.api.get("adb_path", "")) if self.api else ""
            except Exception:
                wanted = ""
            adb = find_adb(wanted)
            if not adb:
                self._note = "adb not found \u2013 install android-tools"
                return
            found = adb_devices(adb)
            if not found:
                self._note = ("no device \u2013 plug it in or use "
                              "adb connect, and accept the prompt in "
                              "the headset")
                return
            self._note = " \u00b7 ".join(f"{s} ({st})" for s, st in found)
        except Exception as e:
            self._note = f"adb failed: {e}"

    def on_refresh(self):
        # refresh() is not in every app build - an old host loses the
        # button, not the panel
        fn = getattr(self.api, "refresh", None) if self.api else None
        if callable(fn):
            try:
                fn()
            except Exception:
                pass

    # ------------------------------------------------------------ poll
    def sync(self):
        mon = self.get_monitor()
        if mon is None:
            self.state.setText("not loaded")
            self._show("The battery block is switched off.")
            return

        snap = mon.snapshot()
        if snap.get("ok"):
            self.state.setText(f"{snap.get('source', '?')} \u00b7 "
                               f"{snap.get('device', '?')}")
        elif snap.get("at"):
            self.state.setText("nothing found")
        else:
            self.state.setText("waiting for the first read \u2026")

        lines = []
        hmd = snap.get("hmd")
        if hmd:
            lines.append(self._line("headset", hmd))
        for c in snap.get("controllers") or []:
            role = {"L": "left", "R": "right"}.get(c.get("role"), "controller")
            lines.append(self._line(role, c))
        for t in snap.get("trackers") or []:
            lines.append(self._line("tracker", t))
        if not lines:
            lines.append(snap.get("error") or "nothing reported yet")
        if self._note:
            lines.append("")
            lines.append(self._note)
        self._show("\n".join(lines))

    @staticmethod
    def _line(what, dev):
        flag = " charging" if dev.get("charging") else ""
        name = dev.get("name") or ""
        return f"{what:<11} {dev.get('pct', '?'):>3}%{flag}  {name}".rstrip()

    def _show(self, text):
        if text != self.view.toPlainText():
            self.view.setPlainText(text)
