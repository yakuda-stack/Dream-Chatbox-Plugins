# vendor/ – what ships inside this plugin

OSCLeash is python and this plugin is python, so the plugin carries it
instead of sending people to the AUR, a release page or an AppImage –
together with everything it imports, OSCQuery included. Needs python
3.10 or newer (zeroconf's minimum).
Installing the plugin installs OSCLeash; the Start button runs the
script in `vendor/OSCLeash/`.

Nothing here is written by yakuda. Everything keeps its own licence.

## OSCLeash

* Upstream: https://github.com/ZenithVal/OSCLeash
* Licence: MIT, © 2022 ZenithVal – full text in `OSCLeash/LICENSE`
* Included: `OSCLeash.py` and `Controllers/`. The Unity prefabs,
  the `Resources/` folder (~8 MB of images), `Scripts/`, `Testing/` and
  the packaging files are **not** included – they are not needed to run
  it, and the avatar side belongs in the upstream repository where
  people can read the setup guide with it.

### The modifications

**1. `Controllers/PackageController.py`** imported `tinyoscquery` at module
level. That made **every** leash need `zeroconf`, including one with
OSCQuery switched off, and a machine without zeroconf could not start
OSCLeash at all. The two imports were moved into the `if useOSCQuery:`
branch – same code, same behaviour, just later. The file carries a
header saying so.

**2. `OSCLeash.py`**, two lines, header in the file:

* The Linux config path was a f-string nested in a f-string with the
  same quotes. That is only valid from python 3.12 on – on 3.10/3.11 the
  whole file is a `SyntaxError` and no leash starts at all. Now built
  with `os.path.join`, same path. (The plugin sets
  `OSCLEASH_CONFIG_PATH` anyway, so that line never decides anything
  here – it only has to compile.)
* `import sys` added. The restart in the error path uses `sys` but
  upstream never imported it, so every startup error ended in a
  `NameError` instead of the real message.

Nothing else was changed. Bug reports about OSCLeash itself belong
upstream; if a problem disappears with the plugin's own copy replaced by
an upstream one, it is this modification's fault and belongs here.

## python-osc  (`vendor/pythonosc`)

* Upstream: https://pypi.org/project/python-osc/ (1.10.2)
* Licence: public domain (Unlicense)
* Why bundled: OSCLeash needs it, and the interpreter that ends up
  running OSCLeash is not always the one the chatbox uses – a frozen
  Windows build has to fall back to a python from `PATH`, which has
  nothing installed.

## tinyoscquery  (`vendor/tinyoscquery`)

* Upstream: https://github.com/Hackebein/tinyoscquery (the fork
  OSCLeash's `requirements.txt` pins, not the PyPI package)
* Licence: MIT, © 2022 CyberKitsune – full text in
  `tinyoscquery/LICENSE`
* Only imported when a leash has **OSCQuery** switched on.

## zeroconf  (`vendor/zeroconf`)

* Upstream: https://github.com/python-zeroconf/python-zeroconf (0.151.3)
* Licence: LGPL-2.1-or-later – full text in `zeroconf/COPYING`
* Why bundled: `tinyoscquery` needs it, so **OSCQuery** needs it. The
  Windows build runs OSCLeash with a python from `PATH`, which almost
  never has zeroconf – so a second leash (OSCQuery is on from the second
  one) could not start there.
* Only the **pure-python** source (`src/zeroconf` from the sdist) is
  included, no compiled `.so`/`.pyd`: zeroconf runs without its Cython
  speedups, identically on Windows and Linux. The `.pxd` files are
  Cython hints and unused. Unmodified; LGPL allows shipping it as long
  as it stays replaceable – delete the folder and an installed zeroconf
  is used instead.

## ifaddr  (`vendor/ifaddr`)

* Upstream: https://github.com/pydron/ifaddr (0.2.0)
* Licence: MIT – `ifaddr/LICENSE.txt`
* Why bundled: zeroconf's only dependency. Pure python, unmodified.

## Updating the bundle

```fish
set tmp (mktemp -d)
curl -sL -o $tmp/o.zip https://codeload.github.com/ZenithVal/OSCLeash/zip/refs/heads/main
unzip -q $tmp/o.zip -d $tmp
cp $tmp/OSCLeash-main/OSCLeash.py vendor/OSCLeash/
cp $tmp/OSCLeash-main/Controllers/*.py vendor/OSCLeash/Controllers/
cp $tmp/OSCLeash-main/LICENSE vendor/OSCLeash/

# zeroconf + ifaddr, pure-python source only
python -m pip download --no-deps --no-binary :all: zeroconf ifaddr -d $tmp
tar xzf $tmp/zeroconf-*.tar.gz -C $tmp; tar xzf $tmp/ifaddr-*.tar.gz -C $tmp
rm -r vendor/zeroconf vendor/ifaddr
cp -r $tmp/zeroconf-*/src/zeroconf vendor/; cp $tmp/zeroconf-*/COPYING vendor/zeroconf/
cp -r $tmp/ifaddr-*/ifaddr vendor/; cp $tmp/ifaddr-*/LICENSE.txt vendor/ifaddr/
```

Then re-apply the modifications above to `PackageController.py` and
`OSCLeash.py`, bump the plugin version, and note the upstream versions
in the changelog. The
`Config.json` upstream ships is deliberately not copied: the plugin
generates one per leash and points at it with `OSCLEASH_CONFIG_PATH`.
