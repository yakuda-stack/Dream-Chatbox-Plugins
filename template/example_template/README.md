# Example Template

A working plugin that shows every part of the plugin API at once. Install
it, switch it on, open its **Settings** – every control is live: the
labels update, the buttons do something, the panel below logs which hook
fired.

## Start your own from it

1. Copy the folder and rename it. The folder name, `"id"` and the module
   folder must match, and the id has to be `[a-z0-9_-]` because it is
   used as a python module name.
2. In `plugin.json`: change `name`, `id`, `version`, `author`,
   `description`, `summary`, and delete every setting you do not need.
3. In `main.py`: delete every hook you do not need. All of them are
   optional.
4. Delete `panel.py` and `build_widget()` unless the plugin really needs
   its own UI – settings alone cover most plugins.
5. Zip the folder (the folder itself must be inside the zip) and install
   it through **Plugins → Install from .zip**.

**Never ship a `configs/` folder.** The installer keeps the existing one
only when the archive has none; shipping one wipes every setting the
user made on update.

## The settings, one per type

| Type | What it is |
| --- | --- |
| `label` | read-only line. `api.set()` rewrites it → live status |
| `bool` | checkbox. Other rows hang off it with `depends` |
| `text` | free text, `"secret": true` masks it |
| `choice` | dropdown, stores the `value` not the label |
| `int` | spinbox with `min` / `max` / `suffix` |
| `slider` | same range, for when direction beats precision |
| `emoji` | text field with the app's icon picker |
| `path` | text field with a file dialog, `"mode": "file"` or `"dir"` |
| `action` | a button → `on_action(key)`, returns text to show |
| `group` | collapsible block, nests two levels |

`depends` hides a row while another setting is off; `depends_value`
compares against one value or a list. Keys are unique across the whole
schema, groups included – option values live in one flat dict.

The last row in the template has type `hologram`, which does not exist.
It is there on purpose: an unknown type is **kept**, its value stays
readable through `api.get()`, and the UI says which version it would
need. Delete that row in your own plugin.

## The hooks, in the order they run

```
setup(api)          once, when switched on
on_settings(opts)   a setting changed
on_tick()           once per chatbox frame
get_values()        → {<id>_<key>} placeholders
get_text()          → {<id>}
get_lines()         → whole lines in the payload
on_text(text)       last look at the finished message
on_action(key)      a button was pressed
on_event(name,data) the app announced something
build_widget(p)     the plugin's own UI
teardown()          switched off, or the app is closing
```

## Three mistakes worth avoiding

**`""` instead of `None` in `get_values()`.** A `None` is dropped
together with its separators, so `"{a} | {b}"` leaves no stray pipe. An
empty string is a value and stays.

**Importing Qt at module level.** Import it inside `build_widget()`, so
the plugin still loads where there is no GUI.

**Touching a widget from a worker thread.** That is a segfault, not an
exception. Poll from a `QTimer` on the GUI thread instead – `panel.py`
shows the pattern. `api.set()` is safe from any thread; the app queues
it onto the GUI thread for you.

## Feature detection

```python
if api.supports("api.set"):
    api.set("status", "ready")
```

Better than declaring `"api": 2` in the manifest, which makes the plugin
refuse to load on an older app. Declare it only for something the plugin
genuinely cannot work without.
