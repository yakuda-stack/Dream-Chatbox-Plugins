"""The new / edit profile dialog, and the category editor.

Imported only when a dialog is actually opened, so a headless run never
touches Qt through this file either.

The parameter picker merges what the profile already stores with what
VRChat reports now, so an avatar that gained a toggle since the profile
was made shows the new toggle unchecked rather than hiding it.

Since the switch to OSCQuery, ``writable`` is VRChat's own answer rather
than a guess from the parameter name, and ``tag`` is the type VRChat
declares rather than whatever python inferred from a UDP packet.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QLineEdit, QPlainTextEdit, QPushButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout)

TAG_LABEL = {"b": "Bool", "i": "Int", "f": "Float", "s": "String"}
ROLE_TAG = int(Qt.ItemDataRole.UserRole) + 1


def _fmt(value, tag):
    if tag == "b":
        return "true" if value else "false"
    if tag == "f":
        try:
            return f"{float(value):.4g}"
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _parse(text, tag):
    text = str(text).strip()
    if tag == "b":
        return text.lower() in ("1", "true", "yes", "on", "wahr")
    if tag == "i":
        return int(float(text))
    if tag == "f":
        return float(text)
    return text


class ProfileDialog(QDialog):
    """Used for both 'new' and 'edit' - the only difference is what it is
    seeded with and what the title says.

    ``live`` is {name: (value, tag, writable_or_None)} plus the current
    avatar id under the reserved key ``__avatar__``.
    """

    def __init__(self, parent, title, profile, live, categories,
                 writable=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(660, 580)
        self.live = dict(live or {})
        self.avatar_now = str(self.live.pop("__avatar__", "") or "")
        self._writable = writable or (lambda _name: True)
        profile = profile or {}

        box = QVBoxLayout(self)
        box.setSpacing(10)

        # ------------------------------------------------------ header
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name"))
        self.name = QLineEdit(str(profile.get("name", "")))
        self.name.setPlaceholderText("Party outfit, blue hair, sleep mode …")
        name_row.addWidget(self.name, 1)
        name_row.addWidget(QLabel("Category"))
        self.category = QComboBox()
        self.category.setEditable(True)
        self.category.setMinimumWidth(180)
        self.category.addItem("")
        for name in categories:
            self.category.addItem(name)
        self.category.setCurrentText(str(profile.get("category", "")))
        self.category.lineEdit().setPlaceholderText("type to create one")
        name_row.addWidget(self.category)
        box.addLayout(name_row)

        self.notes = QPlainTextEdit(str(profile.get("notes", "")))
        self.notes.setPlaceholderText("Notes (optional)")
        self.notes.setFixedHeight(52)
        box.addWidget(self.notes)

        self.avatar_id = str(profile.get("avatar_id", ""))
        self.bind_avatar = QCheckBox(
            "Remember the avatar this was captured on")
        self.bind_avatar.setChecked(bool(self.avatar_id or self.avatar_now))
        self.bind_avatar.setToolTip(
            "Loading a profile onto a different avatar sends parameters that "
            "avatar may not have. VRChat ignores them, but the profile will "
            "not do what its name says either - this is what makes the "
            "warning appear.")
        box.addWidget(self.bind_avatar)

        # ------------------------------------------------------ picker
        tools = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter parameters …")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._refilter)
        tools.addWidget(self.filter, 1)
        for caption, slot in (("All", lambda: self._check_all(True)),
                              ("None", lambda: self._check_all(False)),
                              ("Take live values", self._take_live)):
            btn = QPushButton(caption)
            btn.setFixedHeight(26)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(slot)
            tools.addWidget(btn)
        box.addLayout(tools)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Parameter", "Type", "Value"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSortingEnabled(True)
        self.tree.header().resizeSection(0, 330)
        self.tree.header().resizeSection(1, 70)
        self.tree.itemChanged.connect(self._on_item_changed)
        box.addWidget(self.tree, 1)

        self.hint = QLabel("")
        self.hint.setObjectName("dim")
        self.hint.setWordWrap(True)
        box.addWidget(self.hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        box.addWidget(buttons)

        self._fill(profile.get("params") or {})
        self.name.setFocus()

    # ------------------------------------------------------------ fill
    def _fill(self, stored):
        """Rows are the union of stored and live. Stored ones start
        checked, live-only ones start unchecked - so opening an old
        profile never quietly grows it."""
        # An avatar with a few hundred parameters made opening this
        # dialog visibly slow. Adding items one at a time repaints and
        # re-lays-out the tree on every insert; batching them and taking
        # the signals out of the loop is roughly an order of magnitude.
        self.tree.setUpdatesEnabled(False)
        self.tree.blockSignals(True)
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        names = set(stored) | {n for n in self.live if self._writable(n)}
        hidden = 0
        rows = []
        for name in sorted(names, key=str.casefold):
            entry = stored.get(name)
            if entry is not None:
                tag = str(entry.get("t", "f"))[:1] or "f"
                value = entry.get("v")
                checked = True
            else:
                value, tag, _w = self.live[name]
                checked = False
            if entry is None and not self._writable(name):
                hidden += 1
                continue
            item = QTreeWidgetItem([name, TAG_LABEL.get(tag, tag),
                                    _fmt(value, tag)])
            item.setData(0, ROLE_TAG, tag)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable |
                          Qt.ItemFlag.ItemIsEditable)
            item.setCheckState(0, Qt.CheckState.Checked if checked
                               else Qt.CheckState.Unchecked)
            if name not in self.live:
                item.setToolTip(0, "stored, but not on the current avatar")
                item.setForeground(0, self.palette().mid())
            elif not self._writable(name):
                item.setToolTip(0, "VRChat reports this one as read-only")
                item.setForeground(0, self.palette().mid())
            rows.append(item)
        self.tree.addTopLevelItems(rows)
        self.tree.setSortingEnabled(True)
        self.tree.sortItems(0, Qt.SortOrder.AscendingOrder)
        self.tree.blockSignals(False)
        self.tree.setUpdatesEnabled(True)

        note = (f"{self.tree.topLevelItemCount()} parameter(s). "
                "Double-click a value to change it before saving.")
        if hidden:
            note += (f"  {hidden} read-only parameter(s) hidden - VRChat "
                     "drives those itself.")
        self.hint.setText(note)

    # --------------------------------------------------------- editing
    def _on_item_changed(self, item, column):
        """Validate a hand-typed value against the row's own type rather
        than letting a stray letter become the string '0.5x'."""
        if column != 2:
            return
        tag = item.data(0, ROLE_TAG) or "f"
        try:
            value = _parse(item.text(2), tag)
        except (TypeError, ValueError):
            self.hint.setText(f"'{item.text(2)}' is not a valid "
                              f"{TAG_LABEL.get(tag, tag)} value.")
            return
        self.tree.blockSignals(True)
        item.setText(2, _fmt(value, tag))
        self.tree.blockSignals(False)

    def _refilter(self, text):
        text = text.strip().casefold()
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            item.setHidden(bool(text) and text not in item.text(0).casefold())

    def _check_all(self, state):
        flag = Qt.CheckState.Checked if state else Qt.CheckState.Unchecked
        self.tree.setUpdatesEnabled(False)
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if not item.isHidden():
                item.setCheckState(0, flag)
        self.tree.blockSignals(False)
        self.tree.setUpdatesEnabled(True)

    def _take_live(self):
        """Overwrite every visible row with what VRChat reports now,
        including its declared type - which may differ from what an old
        profile stored if the avatar was rebuilt."""
        taken = 0
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            name = item.text(0)
            if item.isHidden() or name not in self.live:
                continue
            value, tag, _w = self.live[name]
            item.setData(0, ROLE_TAG, tag)
            item.setText(1, TAG_LABEL.get(tag, tag))
            item.setText(2, _fmt(value, tag))
            taken += 1
        self.tree.blockSignals(False)
        self.hint.setText(f"{taken} value(s) taken from the live avatar.")

    # ---------------------------------------------------------- result
    def _accept(self):
        if not self.name.text().strip():
            self.hint.setText("The profile needs a name.")
            self.name.setFocus()
            return
        self.accept()

    def result_profile(self):
        params = {}
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.checkState(0) != Qt.CheckState.Checked:
                continue
            tag = item.data(0, ROLE_TAG) or "f"
            try:
                value = _parse(item.text(2), tag)
            except (TypeError, ValueError):
                continue
            params[item.text(0)] = {"t": tag, "v": value}
        avatar = ""
        if self.bind_avatar.isChecked():
            avatar = self.avatar_now or self.avatar_id
        return {
            "name": self.name.text().strip(),
            "category": self.category.currentText().strip(),
            "notes": self.notes.toPlainText().strip(),
            "avatar_id": avatar,
            "params": params,
        }


class CategoryDialog(QDialog):
    """Rename or delete a category. Deleting one never deletes the
    profiles in it - they fall back to Uncategorized."""

    def __init__(self, parent, categories):
        super().__init__(parent)
        self.setWindowTitle("Categories")
        self.setMinimumWidth(360)
        self.action = None

        box = QVBoxLayout(self)
        box.addWidget(QLabel("Category"))
        self.pick = QComboBox()
        for name in categories:
            self.pick.addItem(name)
        box.addWidget(self.pick)

        box.addWidget(QLabel("New name"))
        self.rename = QLineEdit()
        self.rename.setPlaceholderText("leave empty to only delete")
        box.addWidget(self.rename)

        note = QLabel("Deleting a category keeps its profiles and moves "
                      "them to Uncategorized.")
        note.setObjectName("dim")
        note.setWordWrap(True)
        box.addWidget(note)

        row = QHBoxLayout()
        row.addStretch()
        for caption, action in (("Rename", "rename"), ("Delete", "delete"),
                                ("Close", None)):
            btn = QPushButton(caption)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _c=False, a=action: self._go(a))
            row.addWidget(btn)
        box.addLayout(row)

    def _go(self, action):
        self.action = action
        if action is None:
            self.reject()
        else:
            self.accept()

    def selection(self):
        return self.pick.currentText(), self.rename.text().strip()
