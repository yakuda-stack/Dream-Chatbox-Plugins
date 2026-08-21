"""The panel: search bar, categories, profile list, live parameters.

Read the layout top to bottom and it is the same order as the screen:

    _build_banner()   only visible when something is actually wrong
    _build_head()     search · category filter · + · ⋯
    _build_tree()     profiles, grouped by category, load/save per row
    _build_actions()  what to do with the selected one
    _build_live()     collapsible view of what VRChat reports now
    _build_status()   one line, always truthful about the connection

There is exactly one timer. It polls the bridge and repaints the three
things that move - the banner, the live table and the status line.
Nothing in the receive thread, the watchdog or the apply worker ever
touches a widget, which is the only way to keep a plugin from taking the
whole app down with it.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import time

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QFont
from PyQt6.QtWidgets import (
    QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMenu, QMessageBox, QPushButton, QToolButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget)

from . import discovery as vrcq
from .runtime import state as rt
from .store import UNSORTED

ROLE_ID = int(Qt.ItemDataRole.UserRole) + 1

BTN = ("QPushButton {{ background: {bg}; border: 1px solid {edge};"
       " border-radius: 7px; color: {fg}; padding: 0 12px; }}"
       "QPushButton:hover {{ background: {edge}; }}"
       "QPushButton:disabled {{ color: #6c7382; border-color: #2a2f3a;"
       " background: #1b1f28; }}")
ROW_BTN = ("QPushButton { background: #232733; border: 1px solid #333947;"
           " border-radius: 6px; color: #cfd6e2; padding: 0 10px; }"
           "QPushButton:hover { background: #2b3a4d; border-color: #5b8dc9;"
           " color: #cfe0f5; }")
BANNER = ("QLabel { background: #3a2f24; border: 1px solid #b58a4a;"
          " border-radius: 8px; color: #f0e2cc; padding: 8px 10px; }")

BLUE = ("#2b3a4d", "#5b8dc9", "#cfe0f5")
GREY = ("#232733", "#333947", "#cfd6e2")
RED = ("#3a2a2c", "#b5504a", "#f0d6d4")

TAG_LABEL = {"b": "Bool", "i": "Int", "f": "Float", "s": "String"}


def _button(caption, style, slot, width=None):
    btn = QPushButton(caption)
    btn.setFixedHeight(30)
    if width:
        btn.setFixedWidth(width)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(BTN.format(bg=style[0], edge=style[1], fg=style[2]))
    btn.clicked.connect(slot)
    return btn


def _ago(seconds):
    if seconds < 0:
        return "never"
    if seconds < 2:
        return "just now"
    if seconds < 90:
        return f"{int(seconds)}s ago"
    return f"{int(seconds / 60)}m ago"


class ProfilesPanel(QWidget):
    """One panel, re-used across page rebuilds - see instance()."""

    _instance = None

    @classmethod
    def instance(cls, parent=None):
        if cls._instance is not None:
            try:
                cls._instance.isVisible()
            except RuntimeError:
                cls._instance = None
        if cls._instance is None:
            cls._instance = cls(parent)
        return cls._instance

    def __init__(self, parent=None):
        super().__init__(parent)
        self._live_open = False
        self._live_items = {}      # name -> QTreeWidgetItem, updated in place
        self._live_names = frozenset()
        self._row_widgets = {}     # profile id -> (box, load, save, status)
        self._saving = set()       # ids whose write has not landed yet
        self._applying = None      # id currently being sent
        self._flash_until = 0.0
        self._flash_text = ""

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(8)
        box.addWidget(self._build_banner())
        box.addLayout(self._build_head())
        box.addWidget(self._build_tree(), 1)
        box.addLayout(self._build_actions())
        box.addWidget(self._hline())
        for widget in self._build_live():
            box.addWidget(widget)
        box.addWidget(self._hline())
        box.addWidget(self._build_status())

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.sync)
        self.timer.start(250)
        self.reload()

    # ---------------------------------------------------------- banner
    def _build_banner(self):
        """Hidden unless something needs saying. A permanent warning
        strip is a warning nobody reads."""
        self.banner = QLabel("")
        self.banner.setStyleSheet(BANNER)
        self.banner.setWordWrap(True)
        self.banner.setVisible(False)
        return self.banner

    def _update_banner(self):
        text = self._diagnose()
        # isVisible() is False for a widget whose page has not been shown
        # yet, so guarding on it meant a resolved banner was never
        # cleared - it would reappear, stale, the next time the settings
        # page was opened. isHidden() is the property that tracks what
        # was actually asked for.
        if not text:
            if not self.banner.isHidden():
                self.banner.setVisible(False)
            self.banner.clear()
            return
        if self.banner.text() != text:
            self.banner.setText(text)
        if self.banner.isHidden():
            self.banner.setVisible(True)

    def _diagnose(self):
        """One sentence about what is wrong and what to do, in the order
        the user can act on it.

        Order matters: while the mDNS registration is still running on
        its thread, nothing is wrong yet, and saying so beats the old
        message that announced the plugin was not running while it was
        busy starting.
        """
        if not vrcq.zeroconf_available():
            return ("python-zeroconf is not installed, so VRChat cannot "
                    "discover this plugin.  Install it with  "
                    "sudo pacman -S python-zeroconf")
        bridge, finder = rt.bridge, rt.finder
        if rt.phase == "starting" or (bridge is not None
                                      and bridge.announcing):
            return rt.startup_note or "Announcing over mDNS…"
        if bridge is None or finder is None:
            return ("The plugin is not running - switch it off and on "
                    "again.")
        if bridge.error:
            return bridge.error
        if rt.phase == "error":
            return rt.startup_note or "Startup failed."
        if not bridge.announced:
            return "The OSCQuery service could not be announced."
        if finder.service is None:
            watch = rt.watch_state()
            hint = ("Waiting for VRChat.  Check that OSC is enabled in the "
                    "radial menu (Options → OSC → Enabled) and that "
                    "avahi-daemon is running.")
            if watch.get("retries"):
                hint += f"  Re-announced {watch['retries']}×."
            return hint
        return ""

    # ------------------------------------------------------------ head
    def _build_head(self):
        row = QHBoxLayout()
        row.setSpacing(8)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search profiles, categories, "
                                       "parameters …")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedHeight(30)
        self.search.textChanged.connect(self.refill)
        row.addWidget(self.search, 1)

        self.category = QComboBox()
        self.category.setFixedHeight(30)
        self.category.setMinimumWidth(170)
        self.category.currentIndexChanged.connect(self.refill)
        row.addWidget(self.category)

        row.addWidget(_button("+  New profile", BLUE, self.on_new))
        row.addWidget(_button("⟳", GREY, self.on_refresh_now, width=38))

        self.more = QToolButton()
        self.more.setText("⋯")
        self.more.setFixedSize(34, 30)
        self.more.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.more)
        for caption, slot in (
                ("Manage categories…", self.on_categories),
                (None, None),
                ("Import profiles…", self.on_import),
                ("Export all…", lambda: self.on_export(False)),
                ("Export selected…", lambda: self.on_export(True)),
                (None, None),
                ("Refresh from VRChat now", self.on_refresh_now),
                ("Re-announce and reconnect", self.on_restart),
                ("Open the profiles folder", self.on_folder)):
            if caption is None:
                menu.addSeparator()
                continue
            action = QAction(caption, menu)
            action.triggered.connect(slot)
            menu.addAction(action)
        self.more.setMenu(menu)
        row.addWidget(self.more)
        return row

    # ------------------------------------------------------------ tree
    def _build_tree(self):
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Profile", "Parameters", ""])
        self.tree.setColumnCount(3)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setMinimumHeight(190)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().resizeSection(0, 250)
        self.tree.header().resizeSection(1, 200)
        self.tree.header().resizeSection(2, 178)
        self.tree.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        self.tree.itemSelectionChanged.connect(self._sync_actions)
        return self.tree

    def _build_actions(self):
        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_load = _button("▶  Load", BLUE, self.on_load)
        self.btn_save = _button("⭳  Overwrite", GREY, self.on_overwrite)
        self.btn_edit = _button("Edit…", GREY, self.on_edit)
        self.btn_copy = _button("Duplicate", GREY, self.on_duplicate)
        self.btn_del = _button("Delete", RED, self.on_delete)
        for btn in (self.btn_load, self.btn_save, self.btn_edit,
                    self.btn_copy, self.btn_del):
            row.addWidget(btn)
        row.addStretch()
        self.progress = QLabel("")
        self.progress.setObjectName("dim")
        row.addWidget(self.progress)
        return row

    # ------------------------------------------------------------ live
    def _build_live(self):
        header = QWidget()
        row = QHBoxLayout(header)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        self.live_toggle = QToolButton()
        self.live_toggle.setText("▸  Live parameters")
        self.live_toggle.setCheckable(True)
        self.live_toggle.setAutoRaise(True)
        self.live_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.live_toggle.setStyleSheet(
            "QToolButton { border: none; color: #cfd6e2; padding: 2px 0; }")
        self.live_toggle.toggled.connect(self._toggle_live)
        row.addWidget(self.live_toggle)

        self.live_refresh = QPushButton("⟳  Refresh")
        self.live_refresh.setFixedHeight(24)
        self.live_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self.live_refresh.setStyleSheet(ROW_BTN)
        self.live_refresh.setToolTip(
            "Ask VRChat's OSCQuery server for the full parameter list right "
            "now, instead of waiting for the next poll.")
        self.live_refresh.clicked.connect(self.on_refresh_now)
        row.addWidget(self.live_refresh)
        row.addStretch()

        self.live_tree = QTreeWidget()
        self.live_tree.setHeaderLabels(["Parameter", "Type", "Value",
                                        "Writable"])
        self.live_tree.setRootIsDecorated(False)
        self.live_tree.setAlternatingRowColors(True)
        self.live_tree.setUniformRowHeights(True)
        self.live_tree.setSortingEnabled(True)
        self.live_tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.live_tree.setFixedHeight(170)
        self.live_tree.header().resizeSection(0, 280)
        self.live_tree.header().resizeSection(1, 70)
        self.live_tree.header().resizeSection(2, 110)
        self.live_tree.setVisible(False)
        self.live_tree.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.live_tree.customContextMenuRequested.connect(self._live_menu)
        return header, self.live_tree

    def _toggle_live(self, on):
        self._live_open = on
        self.live_tree.setVisible(on)
        self._live_names = frozenset()   # force one rebuild on open
        self._refresh_live()

    def _build_status(self):
        self.status = QLabel("")
        self.status.setObjectName("dim")
        self.status.setWordWrap(True)
        self.status.setFont(QFont("monospace", 9))
        return self.status

    @staticmethod
    def _hline():
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setObjectName("hline")
        return line

    # ------------------------------------------------------------ data
    @property
    def store(self):
        return rt.store

    @property
    def bridge(self):
        return rt.bridge

    def reload(self):
        current = self.category.currentText() if self.category.count() else ""
        self.category.blockSignals(True)
        self.category.clear()
        self.category.addItem("All categories")
        if self.store is not None:
            for name in self.store.categories():
                self.category.addItem(name)
            if any(not p["category"] for p in self.store.profiles()):
                self.category.addItem(UNSORTED)
        index = self.category.findText(current)
        self.category.setCurrentIndex(max(0, index))
        self.category.blockSignals(False)
        self.refill()

    def refill(self):
        """Group the profiles under their category and hang two buttons
        off every row. Rebuilding beats patching here: the list is a few
        dozen rows and a rebuild cannot go out of sync with the store."""
        if self.store is None:
            return
        keep = self.selected_id()
        needle = self.search.text().strip().casefold()
        wanted = self.category.currentText()
        if self.category.currentIndex() <= 0:
            wanted = None

        self.tree.setUpdatesEnabled(False)
        self._row_widgets.clear()
        self.tree.clear()
        groups = {}
        for profile in sorted(self.store.profiles(),
                              key=lambda p: p["name"].casefold()):
            category = profile["category"] or UNSORTED
            if wanted is not None and category != wanted:
                continue
            if needle and not self._matches(profile, category, needle):
                continue
            groups.setdefault(category, []).append(profile)

        restore = None
        for category in sorted(groups, key=lambda c: (c == UNSORTED,
                                                      c.casefold())):
            profiles = groups[category]
            head = QTreeWidgetItem([category, f"{len(profiles)} profile(s)",
                                    ""])
            head.setFlags(Qt.ItemFlag.ItemIsEnabled)
            font = head.font(0)
            font.setBold(True)
            head.setFont(0, font)
            self.tree.addTopLevelItem(head)
            head.setExpanded(True)
            for profile in profiles:
                item = QTreeWidgetItem([profile["name"],
                                        self._describe(profile), ""])
                item.setData(0, ROLE_ID, profile["id"])
                if profile["notes"]:
                    item.setToolTip(0, profile["notes"])
                head.addChild(item)
                self._attach_row_buttons(item, profile["id"])
                if profile["id"] == keep:
                    restore = item

        if restore is not None:
            self.tree.setCurrentItem(restore)
        self.tree.setUpdatesEnabled(True)
        self._sync_actions()

        if not groups:
            empty = QTreeWidgetItem([
                "No profiles yet" if not needle else "Nothing matches",
                "Press + New profile to capture what the avatar is wearing "
                "right now", ""])
            empty.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.tree.addTopLevelItem(empty)

    @staticmethod
    def _matches(profile, category, needle):
        """Search covers parameter names too, so 'hue' finds the profile
        that touches Hue even when its name is 'summer'."""
        if needle in profile["name"].casefold():
            return True
        if needle in category.casefold():
            return True
        if needle in profile["notes"].casefold():
            return True
        return any(needle in name.casefold() for name in profile["params"])

    def _describe(self, profile):
        count = len(profile["params"])
        parts = [f"{count} parameter{'s' if count != 1 else ''}"]
        avatar = profile["avatar_id"]
        if avatar:
            live = self.bridge.avatar if self.bridge else ""
            parts.append("this avatar" if live and live == avatar
                         else f"avatar {avatar[:12]}…")
        if profile["updated"]:
            parts.append(time.strftime("%Y-%m-%d",
                                       time.localtime(profile["updated"])))
        return " · ".join(parts)

    def _attach_row_buttons(self, item, pid):
        """One widget per row holding both buttons and a status label.

        Keeping them in a single cell is what lets a row say "saving…" or
        "62%" in place: the buttons hide, the label shows, and nothing
        has to be rebuilt while the user is watching it.
        """
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        load = QPushButton("▶ Load")
        save = QPushButton("⭳ Save")
        for btn, slot in ((load, lambda: self.on_load(pid)),
                          (save, lambda: self.on_overwrite(pid))):
            btn.setFixedHeight(22)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(ROW_BTN)
            btn.clicked.connect(slot)
            lay.addWidget(btn)

        status = QLabel("")
        status.setStyleSheet("QLabel { color: #9fb4d0; }")
        status.setVisible(False)
        lay.addWidget(status)
        lay.addStretch()

        self._row_widgets[pid] = (box, load, save, status)
        self.tree.setItemWidget(item, 2, box)
        self._paint_row(pid)

    def _paint_row(self, pid):
        """Show either the buttons or whatever the row is busy doing."""
        parts = self._row_widgets.get(pid)
        if parts is None:
            return
        _box, load, save, status = parts
        try:
            if pid in self._saving:
                text = "saving…"
            elif pid == self._applying:
                state = rt.apply_state()
                total = state.get("total") or 0
                done = state.get("done") or 0
                pct = int(done * 100 / total) if total else 0
                text = f"sending… {pct}%"
            else:
                text = ""
            busy = bool(text)
            if status.text() != text:
                status.setText(text)
            if status.isHidden() == busy:
                status.setVisible(busy)
            if load.isHidden() != busy:
                load.setVisible(not busy)
                save.setVisible(not busy)
        except RuntimeError:
            # the row was rebuilt underneath us; refill() will redo it
            self._row_widgets.pop(pid, None)

    def _paint_busy_rows(self):
        for pid in list(self._row_widgets):
            if pid in self._saving or pid == self._applying:
                self._paint_row(pid)

    def _mark_saving(self, pid):
        """Optimistic: the row is already in the list, the bytes are not
        on disk yet. Cleared by sync() once the writer catches up."""
        self._saving.add(pid)
        self._paint_row(pid)

    # ------------------------------------------------------- selection
    def selected_id(self):
        item = self.tree.currentItem()
        if item is None:
            return None
        return item.data(0, ROLE_ID)

    def selected(self):
        pid = self.selected_id()
        if pid is None or self.store is None:
            return None
        return self.store.by_id(pid)

    def _sync_actions(self):
        has = self.selected_id() is not None
        for btn in (self.btn_load, self.btn_save, self.btn_edit,
                    self.btn_copy, self.btn_del):
            btn.setEnabled(has)

    def _on_double_click(self, item, _column):
        if item.data(0, ROLE_ID):
            self.on_load()

    def _context_menu(self, point):
        item = self.tree.itemAt(point)
        if item is None or not item.data(0, ROLE_ID):
            return
        self.tree.setCurrentItem(item)
        menu = QMenu(self)
        for caption, slot in (("Load", self.on_load),
                              ("Overwrite with live values",
                               self.on_overwrite),
                              ("Edit…", self.on_edit),
                              ("Duplicate", self.on_duplicate),
                              (None, None),
                              ("Delete", self.on_delete)):
            if caption is None:
                menu.addSeparator()
                continue
            action = QAction(caption, menu)
            action.triggered.connect(slot)
            menu.addAction(action)
        menu.exec(self.tree.viewport().mapToGlobal(point))

    # --------------------------------------------------------- actions
    def on_new(self):
        if not self._have_parameters("capture a profile"):
            return
        from .dialogs import ProfileDialog
        dialog = ProfileDialog(self, "New profile", None, self._live_dict(),
                               self.store.categories(), self._writable)
        # a fresh profile starts with everything ticked, which is what
        # "capture what I am wearing right now" means
        dialog._check_all(True)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        data = dialog.result_profile()
        profile = self.store.add(data["name"], data["category"],
                                 data["params"], data["avatar_id"],
                                 data["notes"])
        self.reload()
        self._select(profile["id"])
        self._flash(f"'{profile['name']}' saved with "
                    f"{len(profile['params'])} parameter(s).")

    def on_edit(self):
        profile = self.selected()
        if profile is None:
            return
        from .dialogs import ProfileDialog
        dialog = ProfileDialog(self, f"Edit '{profile['name']}'", profile,
                               self._live_dict(), self.store.categories(),
                               self._writable)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        self.store.update(profile["id"], **dialog.result_profile())
        self.reload()
        self._select(profile["id"])
        self._flash("Saved.")

    def on_overwrite(self, pid=None):
        """Replace the stored values with what VRChat reports now,
        keeping the parameter list. That is the common case: same outfit
        profile, one slider nudged."""
        profile = self.store.by_id(pid) if pid else self.selected()
        if profile is None:
            return
        if not self._have_parameters("overwrite a profile"):
            return
        live = self._live_dict()
        params = dict(profile["params"])
        updated = 0
        for name in list(params):
            if name in live:
                value, tag, _w = live[name]
                params[name] = {"t": tag, "v": value}
                updated += 1
        if not params:
            params = rt.capture()
            updated = len(params)
        self.store.update(profile["id"], params=params,
                          avatar_id=profile["avatar_id"] or
                          (self.bridge.avatar if self.bridge else ""))
        self.refill()
        self._mark_saving(profile["id"])
        self._flash(f"'{profile['name']}' updated - {updated} value(s) "
                    "taken from the live avatar.")

    def on_load(self, pid=None):
        profile = self.store.by_id(pid) if pid else self.selected()
        if profile is None:
            return
        if not profile["params"]:
            self._warn("Empty profile", "This profile has no parameters "
                       "stored. Edit it and tick a few.")
            return
        if self._api_get("warn_avatar", True) and profile["avatar_id"]:
            live = self.bridge.avatar if self.bridge else ""
            if live and live != profile["avatar_id"]:
                answer = QMessageBox.question(
                    self, "Different avatar",
                    f"'{profile['name']}' was captured on another avatar.\n\n"
                    "VRChat ignores parameters the current avatar does not "
                    "have, so nothing breaks - but the result will probably "
                    "not look like the profile name.\n\nSend it anyway?",
                    QMessageBox.StandardButton.Yes |
                    QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
                if answer != QMessageBox.StandardButton.Yes:
                    return
        if rt.apply_profile(profile, on_done=self._on_applied):
            self._applying = profile["id"]
            self._paint_row(profile["id"])
            self._flash(f"Sending '{profile['name']}' …")
        else:
            self._flash("Another profile is still being sent.")

    def _on_applied(self, failed, mismatched):
        """Called from the apply worker. Only writes to plain python
        attributes - the timer picks them up on the GUI thread, because
        touching a widget from here is a segfault, not an exception."""
        if failed:
            self._flash(f"{failed} parameter(s) could not be sent.")
        elif mismatched:
            shown = ", ".join(sorted(mismatched)[:4])
            more = f" and {len(mismatched) - 4} more" if len(mismatched) > 4 \
                else ""
            self._flash(f"Sent, but VRChat still reports a different value "
                        f"for {shown}{more}. Try a longer pause.", 10)
        else:
            self._flash("Done - VRChat confirms every value.")

    def on_duplicate(self):
        profile = self.selected()
        if profile is None:
            return
        copy = self.store.duplicate(profile["id"])
        self.reload()
        self._select(copy["id"])

    def on_delete(self):
        profile = self.selected()
        if profile is None:
            return
        answer = QMessageBox.question(
            self, "Delete profile",
            f"Delete '{profile['name']}'?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.store.remove(profile["id"])
            self.reload()

    def on_categories(self):
        from .dialogs import CategoryDialog
        names = self.store.categories()
        if not names:
            self._warn("No categories", "Give a profile a category first - "
                       "the field in the profile dialog creates one as you "
                       "type.")
            return
        dialog = CategoryDialog(self, names)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        old, new = dialog.selection()
        if dialog.action == "rename":
            if not new:
                self._warn("No name", "Type the new name first.")
                return
            self.store.rename_category(old, new)
        elif dialog.action == "delete":
            self.store.drop_category(old)
        self.reload()

    # ---------------------------------------------------- import/export
    def on_import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import profiles", "", "JSON (*.json);;All files (*)")
        if not path:
            return
        try:
            added = self.store.import_file(path)
        except (OSError, ValueError) as exc:
            self._warn("Import failed", str(exc))
            return
        self.reload()
        self._flash(f"{added} profile(s) imported.")

    def on_export(self, only_selected):
        ids = None
        if only_selected:
            pid = self.selected_id()
            if pid is None:
                self._warn("Nothing selected", "Pick a profile first.")
                return
            ids = {pid}
        path, _ = QFileDialog.getSaveFileName(
            self, "Export profiles", "param-profiles.json", "JSON (*.json)")
        if not path:
            return
        try:
            count = self.store.export(path, ids)
        except OSError as exc:
            self._warn("Export failed", str(exc))
            return
        self._flash(f"{count} profile(s) written to {path}")

    def on_refresh_now(self):
        self._flash(rt.refresh_now())

    def on_restart(self):
        self._flash(rt.restart())

    def on_folder(self):
        self._flash(rt.open_folder())

    # ------------------------------------------------------------ live
    def _live_dict(self):
        """{name: (value, tag, writable)} - what the dialogs expect,
        plus the avatar id under a reserved key."""
        if self.bridge is None:
            return {}
        data = self.bridge.detailed()
        data["__avatar__"] = self.bridge.avatar
        return data

    def _writable(self, name):
        if self.bridge is None:
            return True
        return self.bridge.is_writable(name, self._skip_builtin(),
                                       self._skip_driven())

    def _have_parameters(self, what):
        """Refuse politely rather than saving an empty profile."""
        if self.bridge is not None and self.bridge.count:
            return True
        self._warn(
            "Nothing to capture",
            f"No avatar parameters are known yet, so there is nothing to "
            f"{what}.\n\n"
            "Press ⟳ to ask VRChat for the full list. If that does not help, "
            "check the banner at the top of the panel.")
        return False

    def _live_menu(self, point):
        item = self.live_tree.itemAt(point)
        if item is None:
            return
        menu = QMenu(self)
        copy = QAction(f"Copy name  ({item.text(0)})", menu)
        copy.triggered.connect(lambda: self._copy(item.text(0)))
        menu.addAction(copy)
        single = QAction("New profile from this parameter only", menu)
        single.triggered.connect(lambda: self._new_from_single(item))
        menu.addAction(single)
        menu.exec(self.live_tree.viewport().mapToGlobal(point))

    def _copy(self, text):
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(text)
        self._flash(f"'{text}' copied.")

    def _new_from_single(self, item):
        name = item.text(0)
        live = self._live_dict()
        if name not in live:
            return
        value, tag, _w = live[name]
        profile = self.store.add(name, "", {name: {"t": tag, "v": value}},
                                 self.bridge.avatar if self.bridge else "")
        self.reload()
        self._select(profile["id"])
        self._flash(f"Profile '{name}' created from one parameter.")

    def _refresh_live(self):
        """Update values in place.

        This used to clear and rebuild every row whenever any value
        changed - which, while a profile is being sent, is every 250 ms
        for every parameter VRChat echoes back. Rebuilding a 260-row tree
        that often is most of why loading felt sluggish. Now the rows are
        only rebuilt when the set of parameter *names* changes; a value
        change is three setText calls.
        """
        if self.bridge is None:
            return
        detailed = self.bridge.detailed()
        needle = self.search.text().strip().casefold()
        names = frozenset(n for n in detailed
                          if not needle or needle in n.casefold())

        self.live_toggle.setText(
            ("▾  " if self._live_open else "▸  ") +
            (f"Live parameters ({len(detailed)})" if detailed
             else "Live parameters"))
        if not self._live_open:
            return

        if names != self._live_names:
            self._rebuild_live(detailed, names)
            return

        for name, item in self._live_items.items():
            entry = detailed.get(name)
            if entry is None:
                continue
            value, tag, from_vrc = entry
            shown = self._shown(value, tag)
            if item.text(2) != shown:
                item.setText(2, shown)
            label = TAG_LABEL.get(tag, tag)
            if item.text(1) != label:
                item.setText(1, label)
            mark = self._mark(name, from_vrc)
            if item.text(3) != mark:
                item.setText(3, mark)

    def _rebuild_live(self, detailed, names):
        self._live_names = names
        self._live_items = {}
        self.live_tree.setUpdatesEnabled(False)
        self.live_tree.setSortingEnabled(False)
        self.live_tree.clear()
        rows = []
        for name in sorted(names, key=str.casefold):
            value, tag, from_vrc = detailed[name]
            item = QTreeWidgetItem([name, TAG_LABEL.get(tag, tag),
                                    self._shown(value, tag),
                                    self._mark(name, from_vrc)])
            if not self._writable(name):
                item.setToolTip(3, "VRChat drives this one itself - it is "
                                   "left out of a capture.")
                item.setForeground(0, self.palette().mid())
            elif from_vrc is None:
                item.setToolTip(3, "Guessed from the name; VRChat has not "
                                   "been asked yet. Press Refresh.")
            self._live_items[name] = item
            rows.append(item)
        self.live_tree.addTopLevelItems(rows)   # bulk beats one at a time
        header = self.live_tree.header()
        self.live_tree.setSortingEnabled(True)
        self.live_tree.sortItems(max(0, header.sortIndicatorSection()),
                                 header.sortIndicatorOrder())
        self.live_tree.setUpdatesEnabled(True)

    @staticmethod
    def _shown(value, tag):
        if tag == "b":
            return "true" if value else "false"
        if tag == "f" and isinstance(value, float):
            return f"{value:.4g}"
        return str(value)

    def _mark(self, name, from_vrc):
        """'game' means VRChat said no; 'guess' means we are still going
        by the name because the tree has not arrived yet."""
        writable = self._writable(name)
        if from_vrc is None:
            return "yes*" if writable else "guess"
        return "yes" if writable else "game"

    # ---------------------------------------------------------- poll
    def sync(self):
        self._update_banner()

        # the write queue has drained - the rows can stop saying "saving…"
        if self._saving and rt.store is not None and not rt.store.pending():
            done, self._saving = self._saving, set()
            for pid in done:
                self._paint_row(pid)

        state = rt.apply_state()
        if state["active"]:
            self.progress.setText(f"sending {state['name']} … "
                                  f"{state['done']}/{state['total']}")
        else:
            if self._applying is not None:
                finished, self._applying = self._applying, None
                self._paint_row(finished)
            if time.time() < self._flash_until:
                self.progress.setText(self._flash_text)
            else:
                self.progress.setText("")
        self._paint_busy_rows()

        if self._live_open:
            self._refresh_live()

        if self.bridge is None:
            self.status.setText("starting…" if rt.phase == "starting"
                                else "not running")
            return
        info = self.bridge.status()
        found = rt.finder.status() if rt.finder else {}
        watch = rt.watch_state()

        if found.get("found"):
            where = (f"VRChat {found['name'] or ''} → sending to "
                     f"{self.bridge.send_port}").strip()
        elif rt.phase == "starting" or info.get("announcing"):
            where = rt.startup_note or "announcing…"
        elif info["announced"]:
            where = f"announced tcp/{info['http']} · VRChat not found yet"
        else:
            where = "not announced"
        pulled = (f" ({info['from_tree']} from OSCQuery)"
                  if info["from_tree"] else "")
        polls = f" · {watch['polls']} polls" if watch.get("polls") else ""
        self.status.setText(
            f"{where}  ·  {info['params']} parameters{pulled}  ·  last "
            f"update {_ago(info['age'])}{polls}  ·  "
            f"{info['avatar'] or 'unknown avatar'}")

    # --------------------------------------------------------- helpers
    def _select(self, pid):
        for i in range(self.tree.topLevelItemCount()):
            head = self.tree.topLevelItem(i)
            for j in range(head.childCount()):
                child = head.child(j)
                if child.data(0, ROLE_ID) == pid:
                    self.tree.setCurrentItem(child)
                    return

    def _flash(self, text, seconds=6):
        self._flash_text = str(text or "")
        self._flash_until = time.time() + seconds

    def _warn(self, title, text):
        QMessageBox.information(self, title, text)

    @staticmethod
    def _api_get(key, default=None):
        api = rt.api
        return api.get(key, default) if api is not None else default

    def _skip_builtin(self):
        return bool(self._api_get("skip_builtin", True))

    def _skip_driven(self):
        return bool(self._api_get("skip_driven", True))
