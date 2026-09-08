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
   `description`, `short_description`, and delete every setting you do
   not need.
3. In `main.py`: delete every hook you do not need. All of them are
   optional.
4. Delete `panel.py` and `build_widget()` unless the plugin really needs
   its own UI – settings alone cover most plugins.
5. Zip the folder (the folder itself must be inside the zip) and install
   it through **Plugins → Install from .zip**.

**Never ship a `configs/` folder.** The installer keeps the existing one
only when the archive has none; shipping one wipes every setting the
user made on update.

## The two descriptions

`plugin.json` carries both, and this template shows the difference:

| Key | Where it shows | How long |
| --- | --- | --- |
| `short_description` | the row in **Plugins → Installed** | one line |
| `description` | the store page, and the row's tooltip | as long as it needs to be |

`short_description` is optional. Without it the list falls back to
`description`, which is why a plugin that explains itself in a paragraph
used to make its row three lines tall. Write both: the long one for
somebody deciding whether to install, the short one for somebody
scrolling past twelve installed plugins.

`summary` is the older spelling of `short_description` and still works —
use one of the two, not both.

## The long text: `about`

JSON has no multi-line string, so a `description` of any length arrives
as one unbroken paragraph. `about` is the way out and takes three
shapes:

```json
"about": "one string"
"about": ["line", "", "line"]
"about": {"format": "markdown", "text": ["## Title", "", "- point"]}
```

This template uses the third one — open its store page and you will see
headings and a numbered list instead of a wall of text.

**Keep `description` a plain string anyway.** An older app fetches your
`plugin.json` from the store over the network and calls `str()` on
whatever it finds there; a list would show up as `['line', 'line']`.
Unknown keys have always been carried along invisibly instead, so
`about` costs those users nothing and `description` is what they read.
Write both.

## The Unity link

```json
"unity": "https://github.com/…/releases/download/v2.2.0/OSCLeash.prefab"
```

A prefab or `.unitypackage` that belongs with the plugin. It shows up as
a 🧩 button next to *Open on GitHub* on the store page and as a link in
the ⓘ popup of the installed plugin, both labelled with the file name so
it is clear what a click downloads.

Only `http(s)` links with a host survive — `file://`, a scheme-less
`github.com/…` or a URL with a space in it are dropped without a word,
because the value goes straight to the desktop's URL handler.

**The link in this template points at somebody else's prefab**
(ZenithVal's OSCLeash) purely so the button has something real to open.
Replace or delete it in your own plugin.

## When the plugin is not about the chatbox

```json
"chatbox": {"enabled": false, "user_editable": true}
```

`enabled: false` means the app never asks for a line — `get_text()` and
`get_lines()` are not called, and the chatbox block disappears from the
card. The questions in it ("own line?", "custom string?") have no
meaningful answer for a plugin that only runs something in the
background.

**`get_values()` keeps running.** The switch is about the plugin writing
to the chatbox itself, not about its placeholders: a plugin can have
nothing to say there and still offer `{its_name}` for somebody else's
line.

`user_editable: true` gives the user a *Send to the chatbox* switch.
This template leaves it on so you can see both halves — flip it and
watch `{example_template}` go quiet while `{example_template_mood}`
keeps working.

## `osc_advanced_example.py` — inactive, and probably not for you

The folder carries one file nothing imports. It shows a plugin opening
its **own** UDP port with its own OSCQuery service, and the header says
plainly when that is the wrong tool.

You do not need it to put text in the chatbox (`get_text()` does that),
to read avatar parameters (the app receives them already — switch OSC
input on in Options), or to send parameters to VRChat (the app has
already discovered the running client's real input port, which is often
not 9000). For those three, a plugin that opens sockets is strictly
worse than one that does not.

It is for the narrow case where something **other than VRChat** has to
reach your plugin on a port it was told about in advance: a hardware
bridge, a heart-rate app with a configurable OSC destination, an
OSCLeash-style second tool. If you are unsure which side you are on, you
are on the first one — delete the file.

What it demonstrates, each being where a first attempt goes wrong:
binding a port that may be taken and falling back instead of silently
sharing it, a receive thread that can actually be stopped, announcing
over mDNS without freezing the GUI, and a teardown whose order and final
`join()` are what make reopening on the same port work.

## `_reference`: everything else, switched off

JSON has no comment syntax, so this file uses a leading underscore
instead: the app keeps keys it does not know in `Plugin.extra` and never
acts on them, which makes `_anything` a safe place to park a setting you
are not using yet.

`_reference` in `plugin.json` holds every manifest key and every setting
type this app understands, each with a short note — including the ones
this template does not use itself (`global_placeholders`, `summary`,
`enabled`, `depends`) and the made-up `hologram` type that shows what an
app does with a row it does not recognise. Move a key out of the block
and drop the underscore to switch it on.

The whole block can be deleted. Nothing reads it.

## Where the blocks go: `layout`

A plugin card's body is three blocks, rendered in this order unless you
say otherwise:

| Block | What is in it |
| --- | --- |
| `chatbox` | own line, custom string, the placeholder hint |
| `settings` | your `settings` rows |
| `widget` | `build_widget()` |

```json
"layout": ["settings", "chatbox"]
```

Names you leave out are appended, so `["widget"]` means "panel first,
the rest as before" — never "panel only". Unknown names are dropped.
Neither rule can take a card apart: a typo costs nothing, and a block
added in a later app version lands at the end instead of disappearing.

### Placing the panel exactly

For "above Connection" rather than "above everything", put a row in the
schema instead — this template has one right at the top:

```json
{"key": "panel_here", "type": "widget"}
```

It may sit anywhere, groups included. It holds no value and never
reaches `config.json`; the key is only there to stay unique, the same
way an `action` key is. When such a row exists, the standalone `widget`
block is dropped so the panel appears exactly once — which is why
`"layout"` here only names two blocks. A second `widget` row finds the
panel already placed, renders nothing and says so in the log.

Delete the row from `settings` to see the other form: the panel becomes
its own block again and `"layout"` can move it around.

An older app turns the row into the usual locked placeholder ("needs a
newer app") and shows the panel at the bottom as before — cosmetically
odd there, functionally intact. That is also why this plugin still
declares `"api": 2`: nothing here is a hard requirement.

### Letting the user rearrange

```json
"user_reorderable": true
```

Each block then gets a 2×3 grip and its own frame, and the user drags
them into whatever order they like. It is stored per plugin in
`configs/config.json` under `"layout"` and survives a restart; your
`layout` is what somebody who never touched it sees.

Off by default on purpose — a plugin whose panel only makes sense
underneath its settings should not have to defend that arrangement.
Turning it off later does not delete an order a user already made: it is
ignored while the switch is off and comes back if you turn it on again.

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
| `widget` | not a setting: places `build_widget()` at that spot |

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

`setup()` in this template asks for three of the newer capabilities and
writes the answers into the debug console:

```
settings.widget    manifest.layout    manifest.about
```

None of them is worth an `"api"` bump — an app that has never heard of
`"layout"` just renders the card in its usual order, which is a fine
outcome.
