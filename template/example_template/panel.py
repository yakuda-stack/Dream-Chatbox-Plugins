"""The custom widget half of the template.

Everything Qt lives in this file and nothing imports it until
build_widget() is called, so the plugin still loads in a headless
manager - a test run, a future CLI.

The panel polls with a QTimer instead of receiving signals from
somewhere else. That is deliberate: polling on the GUI thread cannot
race with anything, while a signal emitted from a worker thread can
reach a widget that is halfway through being deleted.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout,
    QWidget)

BTN = ("QPushButton {{ background: {bg}; border: 1px solid {edge};"
       " border-radius: 8px; color: {fg}; padding: 0 14px; }}"
       "QPushButton:hover {{ background: {edge}; }}")


class TemplatePanel(QWidget):
    """One panel per plugin, re-used across page rebuilds."""

    _instance = None

    @classmethod
    def instance(cls, api, events, parent=None):
        """Hand back the existing panel, or build a fresh one.

        The page rebuilds whenever the plugin list changes, which
        deletes the embedded widget on the C++ side. The python object
        survives that, so touching it raises RuntimeError - which is the
        signal to build a new one rather than a crash.
        """
        if cls._instance is not None:
            try:
                cls._instance.isVisible()
            except RuntimeError:
                cls._instance = None
        if cls._instance is None:
            cls._instance = cls(api, events, parent)
        return cls._instance

    def __init__(self, api, events, parent=None):
        super().__init__(parent)
        self.api = api
        self.events = events

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(8)

        head = QLabel("This part is build_widget() – a plain QWidget the "
                      "plugin builds itself.")
        head.setObjectName("dim")
        head.setWordWrap(True)
        box.addWidget(head)

        row = QHBoxLayout()
        row.setSpacing(8)
        for caption, style, slot in (
                ("Set status", ("#2b3a4d", "#5b8dc9", "#cfe0f5"), self.on_set),
                ("Refresh preview", ("#232733", "#333947", "#cfd6e2"),
                 self.on_refresh),
                ("Clear log", ("#3a2a2c", "#b5504a", "#f0d6d4"),
                 self.on_clear)):
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

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(200)
        self.log.setFixedHeight(120)
        self.log.setFont(QFont("monospace", 9))
        self.log.setStyleSheet(
            "QPlainTextEdit { background: #0f1116; color: #c8d2e0;"
            " border: 1px solid #333947; border-radius: 8px; padding: 6px; }")
        box.addWidget(self.log)

        foot = QLabel("The log shows what on_event() received. Nothing here "
                      "is required – delete this file and drop build_widget() "
                      "if your plugin only needs settings.")
        foot.setObjectName("dim")
        foot.setWordWrap(True)
        box.addWidget(foot)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.sync)
        self.timer.start(500)
        self.sync()

    # ---------------------------------------------------------- actions
    def on_set(self):
        # api.set() writes one of the plugin's own settings and the
        # widget showing it follows along - here that is the "status"
        # label row up in the settings
        self.api.set("status", "set from the panel")

    def on_refresh(self):
        # ask the app to re-render the chatbox preview, for data that
        # arrived between frames
        self.api.refresh()

    def on_clear(self):
        self.events.clear()
        self.log.clear()

    # ------------------------------------------------------------ poll
    def sync(self):
        self.state.setText(f"mood: {self.api.get('mood', '?')} \u00B7 "
                           f"level: {self.api.get('level', 0)}%")
        text = "\n".join(f"{when}  {name}" for when, name in self.events)
        if text != self.log.toPlainText():
            self.log.setPlainText(text)
            self.log.verticalScrollBar().setValue(
                self.log.verticalScrollBar().maximum())
