"""{file_text} {file_text_2} {file_text_3} - text from files.

For everything that already writes a text file: a now-playing file for
OBS (Tuna, Snip, ...), a counter your own script keeps, a note you edit
by hand. The file is read again only when it changed (mtime + size), so
three files cost three stat() calls per frame and nothing else.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import os
from pathlib import Path

SLOTS = (("file_text", "file_path"), ("file_text_2", "file_path_2"),
         ("file_text_3", "file_path_3"))
MAX_BYTES = 16 * 1024          # a chatbox holds 144 characters anyway

_cache = {}                    # path -> (mtime, size, text)


def read(path):
    """Contents of `path`, cached by mtime and size. None when missing."""
    path = str(Path(path).expanduser())
    try:
        st = os.stat(path)
    except OSError:
        _cache.pop(path, None)
        return None
    hit = _cache.get(path)
    if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
        return hit[2]
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_BYTES)
    except OSError:
        return None
    # utf-8 first (what everything on Linux writes), then the Windows
    # code page tools like Snip still use
    for enc in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", "replace")
    _cache[path] = (st.st_mtime, st.st_size, text)
    return text


def shape(text, keep_lines, limit):
    lines = [ln.strip() for ln in text.replace("\r", "").split("\n")]
    lines = [ln for ln in lines if ln]
    text = "\n".join(lines) if keep_lines else " ".join(lines)
    if limit and len(text) > limit:
        text = text[:limit].rstrip()
    return text or None


def values(get, vals):
    if not get("files", False):
        return
    keep = bool(get("file_keep_lines", False))
    try:
        limit = int(get("file_max", 60) or 0)
    except (TypeError, ValueError):
        limit = 60
    for key, path_key in SLOTS:
        path = str(get(path_key, "") or "").strip()
        if not path:
            continue
        text = read(path)
        if text is not None:
            vals[key] = shape(text, keep, limit)
