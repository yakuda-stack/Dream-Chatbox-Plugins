"""Windows only: a private python for OSCLeash, one button away.

The Windows build of the chatbox is a PyInstaller exe - there is a python
inside it, but no python.exe the plugin could hand a script to. Most
Windows users have none on PATH either, so without this the bundled
OSCLeash simply cannot start there.

The "Install missing components" button in the panel fetches the
official *embeddable* python from python.org (a ~10 MB zip, no
installer) and unpacks it to

    %LOCALAPPDATA%\\OSC-DreamChatbox\\oscleash-python\\

No admin rights, no PATH entry, no registry, nothing in "Apps & features"
- deleting that folder undoes it completely. pip is not needed: every
library OSCLeash imports already ships in vendor/.

The embeddable python runs isolated (its python3XX._pth file): it ignores
PYTHONPATH and does not put the script's folder on sys.path. That is what
bootstrap.py is for - the plugin always starts OSCLeash through it.

Everything here runs in a worker thread and only writes to STATE; the
panel polls STATE on its timer. No Qt in here, no console window anywhere
(the download is urllib inside this process, the one test run of the new
python uses CREATE_NO_WINDOW).
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import platform
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

IS_WINDOWS = os.name == "nt"

# newest first; the next one is only tried when python.org answers 404
VERSIONS = ("3.14.7", "3.13.15")
URL = "https://www.python.org/ftp/python/{v}/python-{v}-embed-{arch}.zip"
# the real zip is ~10 MB - anything far above that is not what we asked for
MAX_BYTES = 60 * 1024 * 1024

STATE = {"busy": False, "pct": 0, "msg": "", "error": ""}
_lock = threading.Lock()


def _arch():
    machine = platform.machine().lower()
    if machine in ("amd64", "x86_64"):
        return "amd64"
    if machine in ("arm64", "aarch64"):
        return "arm64"
    return ""


def available():
    """Whether the button makes sense on this machine at all."""
    return IS_WINDOWS and bool(_arch())


def base_dir():
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / "OSC-DreamChatbox" / "oscleash-python"


def exe_path():
    return base_dir() / "python.exe"


def installed():
    return IS_WINDOWS and exe_path().is_file()


def version():
    """The version that was installed, for the panel line."""
    try:
        return (base_dir() / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


# ------------------------------------------------------------ install
def start_install():
    """Kick off the download in the background. Returns False when one
    is already running."""
    with _lock:
        if STATE["busy"]:
            return False
        STATE.update(busy=True, pct=0, msg="starting…", error="")
    threading.Thread(target=_install, name="oscleash-pyembed",
                     daemon=True).start()
    return True


def _set(**kw):
    with _lock:
        STATE.update(**kw)


def _download(url, target):
    req = urllib.request.Request(url, headers={"User-Agent": "OSC-DreamChatbox"})
    with urllib.request.urlopen(req, timeout=30) as resp, open(target, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        if total > MAX_BYTES:
            raise ValueError(f"unexpected download size ({total} bytes)")
        done = 0
        while True:
            chunk = resp.read(64 * 1024)
            if not chunk:
                break
            done += len(chunk)
            if done > MAX_BYTES:
                raise ValueError("download larger than expected - aborted")
            out.write(chunk)
            if total:
                _set(pct=int(done * 100 / total))


def _check_zip(path):
    """A real embeddable zip: readable, uncorrupted, python.exe inside,
    and no entry trying to escape the target folder."""
    if not zipfile.is_zipfile(path):
        raise ValueError("the download is not a zip file")
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        if "python.exe" not in names:
            raise ValueError("python.exe missing from the download")
        for name in names:
            if name.startswith(("/", "\\")) or ".." in Path(name).parts:
                raise ValueError(f"suspicious path in the zip: {name}")
        bad = zf.testzip()
        if bad:
            raise ValueError(f"corrupted file in the zip: {bad}")


def _test_run(exe):
    """Run the fresh python once - hidden - and make sure it really is a
    working python 3.10+ before it replaces anything."""
    kwargs = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    res = subprocess.run(
        [str(exe), "-c",
         "import sys, ssl, ctypes, asyncio, http.server;"
         "sys.exit(0 if sys.version_info >= (3, 10) else 1)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, errors="replace",
        timeout=60, **kwargs)
    if res.returncode != 0:
        raise ValueError(f"the new python does not start: {res.stdout.strip()[:200]}")


def _install():
    target = base_dir()
    work = target.parent / "oscleash-python.new"
    zip_path = target.parent / "oscleash-python.zip"
    try:
        arch = _arch()
        if not arch:
            raise ValueError(f"no embeddable python for {platform.machine()}")
        target.parent.mkdir(parents=True, exist_ok=True)

        got = ""
        for ver in VERSIONS:
            url = URL.format(v=ver, arch=arch)
            _set(msg=f"downloading python {ver}…", pct=0)
            try:
                _download(url, zip_path)
                got = ver
                break
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    raise
        if not got:
            raise ValueError("python.org has none of the expected versions")

        _set(msg="checking…", pct=100)
        _check_zip(zip_path)

        _set(msg="unpacking…")
        shutil.rmtree(work, ignore_errors=True)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(work)
        (work / "VERSION").write_text(got, encoding="utf-8")
        _test_run(work / "python.exe")

        # only now touch the old one: a failed update leaves it working
        shutil.rmtree(target, ignore_errors=True)
        work.rename(target)
        _set(busy=False, msg=f"python {got} installed", error="")
    except Exception as e:           # noqa: BLE001 - shown to the user
        shutil.rmtree(work, ignore_errors=True)
        _set(busy=False, msg="", error=f"install failed: {e}")
    finally:
        try:
            zip_path.unlink()
        except OSError:
            pass
