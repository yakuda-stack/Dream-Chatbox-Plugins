# vendor/ – what ships inside this plugin

OSCLeash is python and this plugin is python, so the plugin carries it
instead of sending people to the AUR, a release page or an AppImage.
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

### The one modification

`Controllers/PackageController.py` imported `tinyoscquery` at module
level. That made **every** leash need `zeroconf`, including one with
OSCQuery switched off, and a machine without zeroconf could not start
OSCLeash at all. The two imports were moved into the `if useOSCQuery:`
branch – same code, same behaviour, just later. The file carries a
header saying so.

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

## Not bundled: zeroconf

`tinyoscquery` needs it, so **OSCQuery** needs it. It is LGPL and ships
platform-specific compiled parts, which is the wrong thing to copy into
a plugin folder – and the chatbox already depends on it, so on a normal
install it is simply there.

When it is not, the plugin says so before starting instead of letting it
fail: OSCLeash's own error path raises a `NameError` (its restart branch
uses `sys` without importing it), so the real cause would never reach
the user.

## Updating the bundle

```fish
set tmp (mktemp -d)
curl -sL -o $tmp/o.zip https://codeload.github.com/ZenithVal/OSCLeash/zip/refs/heads/main
unzip -q $tmp/o.zip -d $tmp
cp $tmp/OSCLeash-main/OSCLeash.py vendor/OSCLeash/
cp $tmp/OSCLeash-main/Controllers/*.py vendor/OSCLeash/Controllers/
cp $tmp/OSCLeash-main/LICENSE vendor/OSCLeash/
```

Then re-apply the modification above to `PackageController.py`, bump the
plugin version, and note the upstream version in the changelog. The
`Config.json` upstream ships is deliberately not copied: the plugin
generates one per leash and points at it with `OSCLEASH_CONFIG_PATH`.
