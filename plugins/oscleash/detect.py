"""Finding the OSCLeash executable.

OSCLeash ships in five shapes and the plugin has to cope with all of
them, because none of them is "the" Linux install:

    AUR            /usr/bin/OSCLeash            (also -nuitka)
    AppImage       ~/Applications/OSCLeash.appimage
    PyInstaller    a plain ELF binary somewhere the user dropped it
    Source         OSCLeash.py next to Controllers/
    Windows        %LocalAppData%\\Programs\\OSCLeash\\OSCLeash.exe

The user can always override the result with the "OSCLeash path"
setting; autodetect only fills in the blank.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import shutil
from pathlib import Path

IS_WINDOWS = os.name == "nt"

# tried in this order, so a proper install wins over a stray download
WHICH_NAMES = ("OSCLeash", "oscleash", "OSCLeash-nuitka", "OSCLeash-Nuitka",
               "OSCLeash-pyinstaller")

FILE_NAMES = ("OSCLeash", "OSCLeash.appimage", "OSCLeash.AppImage",
              "OSCLeash-x86_64.AppImage", "OSCLeash-Nuitka",
              "OSCLeash-pyinstaller", "OSCLeash.exe", "OSCLeash.py")


def _search_dirs():
    home = Path.home()
    dirs = [home / ".local/bin", home / "bin", home / "Applications",
            home / "AppImages", home / "Apps", home / "Downloads",
            Path("/opt/OSCLeash"), Path("/usr/lib/OSCLeash"),
            Path("/usr/share/OSCLeash")]
    if IS_WINDOWS:
        local = os.environ.get("LocalAppData", "")
        if local:
            dirs.insert(0, Path(local) / "Programs" / "OSCLeash")
    # a source checkout, wherever people usually keep one
    for parent in (home, home / "git", home / "Git", home / "Projects",
                   home / "Documents"):
        dirs.append(parent / "OSCLeash")
    return dirs


def find_binary():
    """Best guess at an OSCLeash to run, or "" when there is none.

    The bundled copy wins: it ships with the plugin, it is the version
    this plugin was tested against, and it needs nothing installed. The
    system search below is only a fallback for someone who deliberately
    keeps their own build - and the path setting overrides both.
    """
    from .runtime import bundled_script
    bundled = bundled_script()
    if bundled:
        return bundled
    for name in WHICH_NAMES:
        hit = shutil.which(name)
        if hit:
            return hit
    for folder in _search_dirs():
        try:
            if not folder.is_dir():
                continue
        except OSError:          # unreadable mount, permission denied
            continue
        for name in FILE_NAMES:
            candidate = folder / name
            if candidate.is_file():
                return str(candidate)
    return ""


def kind_of(path):
    """binary | appimage | source – decides how the process is started."""
    name = Path(path).name.lower()
    if name.endswith(".py"):
        return "source"
    if "appimage" in name:
        return "appimage"
    return "binary"


def build_command(path):
    """The argv list for one instance, or None when the path is unusable.

    A .py is started with a real interpreter from installer.python_exe()
    rather than with sys.executable: in a PyInstaller build the latter is
    the chatbox itself, and handing it a script argument would open a
    second chatbox instead of running OSCLeash.
    """
    path = str(path or "").strip()
    if not path:
        return None
    p = Path(path).expanduser()
    if not p.is_file():
        return None
    if kind_of(p) == "source":
        from .runtime import python_exe
        exe = python_exe()
        if not exe:
            return None
        # -W: upstream OSCLeash.py builds a Windows path in an f-string
        # ("\Programs\OSCLeash"), which python reports as an invalid
        # escape sequence on every single start. It is harmless, it is
        # not our file to fix, and two lines of noise at the top of every
        # debug log is two lines people ask about.
        return [exe, "-W", "ignore::SyntaxWarning", str(p)]
    return [str(p)]


def workdir_for(path):
    """OSCLeash's source build imports Controllers/ relative to the file,
    so the working directory has to be the checkout, not the chatbox."""
    return str(Path(path).expanduser().parent)


def is_executable(path):
    p = Path(str(path)).expanduser()
    if not p.is_file():
        return False
    if kind_of(p) == "source" or IS_WINDOWS:
        return True
    return os.access(str(p), os.X_OK)
