"""Hello World – example plugin for OSC-DreamChatbox.

Shows the three things a plugin usually needs:
  * settings declared in plugin.json, read back via api.settings
  * get_text()   -> fills the {hello_world} placeholder
  * get_values() -> fills {hello_world_greeting} and {hello_world_mood}

Nothing here imports Qt: the settings UI is generated from the manifest.
"""

_api = None


def setup(api):
    """Called when the plugin is enabled/loaded."""
    global _api
    _api = api
    api.log(f"ready, greeting is {api.get('greeting')!r}")


def teardown():
    """Called when the plugin is disabled or the app closes."""
    if _api:
        _api.log("bye")


def on_settings(settings):
    """Called whenever the user changed one of the settings above."""
    if _api:
        _api.log(f"settings updated: {settings}")


def _parts():
    greeting = (_api.get("greeting", "Hello") if _api else "Hello").strip()
    mood = (_api.get("mood", "") if _api else "").strip()
    repeat = int(_api.get("repeat", 1) if _api else 1)
    if _api and _api.get("shout"):
        greeting = greeting.upper()
    return greeting, mood * max(1, repeat)


def get_text():
    """The plugin's main output -> {hello_world}."""
    greeting, mood = _parts()
    return f"{greeting} {mood}".strip()


def get_lines():
    """Used when the custom string is switched OFF."""
    text = get_text()
    return [text] if text else []


def get_values():
    """Extra placeholders -> {hello_world_greeting}, {hello_world_mood}."""
    greeting, mood = _parts()
    return {"greeting": greeting, "mood": mood}


def on_text(text):
    """Last-chance filter on the finished chatbox text (unused here)."""
    return text
