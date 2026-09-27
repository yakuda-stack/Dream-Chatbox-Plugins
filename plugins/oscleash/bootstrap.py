"""Starts OSCLeash with the right sys.path, whatever python runs it.

    python bootstrap.py <path/to/OSCLeash.py>

The plugin never runs OSCLeash.py directly any more. The private
embeddable python on Windows (see pyembed.py) runs *isolated*: it
ignores PYTHONPATH and does not add the script's own folder to sys.path,
so OSCLeash would find neither the libraries in vendor/ nor its own
Controllers/. This puts both in front and then runs the script exactly
as `python OSCLeash.py` would.

sys.argv keeps this file in front of the script on purpose: OSCLeash
restarts itself with os.execl(sys.executable, *sys.argv) after an error,
and that restart has to come back through here too.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: bootstrap.py <script.py> [args…]")
    script = os.path.abspath(sys.argv[1])
    # the plugin folder itself must not be importable from OSCLeash -
    # main.py / runtime.py there are the chatbox side, not OSCLeash's
    sys.path[:] = [p for p in sys.path
                   if os.path.abspath(p or os.curdir) != HERE]
    for path in (os.path.join(HERE, "vendor"), os.path.dirname(script)):
        if path not in sys.path:
            sys.path.insert(0, path)
    runpy.run_path(script, run_name="__main__")


if __name__ == "__main__":
    main()
