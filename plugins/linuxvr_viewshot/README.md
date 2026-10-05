# LinuxVR-ViewShot

Shows the translations of **[LinuxVR-ViewShot](https://github.com/yakuda-stack/LinuxVR-ViewShot)** in your chatbox: take a photo of a sign in VR, ViewShot translates it, the translation is in the chatbox for **2–10 seconds** – then your normal chatbox comes back by itself.

## Setup

1. **ViewShot** (1.0.1+): Options → General → **Output** → switch on **📡 Send via OSC** (port `9025`) *or* **📄 Write to a file**.
2. **Here:** pick the same source (OSC is the default and matches ViewShot's defaults).
3. Press **Test** – a test line appears in the chatbox.

## Settings

| Setting | What it does |
| :--- | :--- |
| Get translations by | 📡 OSC on `127.0.0.1:<port>` or 📄 `translation.json` |
| Show for | 2–10 s, then the normal chatbox again |
| In the chatbox | **Replace** (only the translation) · **On top** of your normal lines · **Only the placeholder** |
| Look | prefix (🌐), everything in one line, also the original text |
| Which translations | 📸 photos and/or 🔁 Lens (live translation) |

Translations are cut at 144 characters (the chatbox limit).

## Placeholders

| Placeholder | Value |
| :--- | :--- |
| `{linuxvr_viewshot_translation}` | the translation – only while it is shown |
| `{linuxvr_viewshot_original}` | the recognised text – only while shown |
| `{linuxvr_viewshot_source}` | `photo` / `lens` / `test` – only while shown |
| `{linuxvr_viewshot_last}` | the latest translation, stays until the next one |

## What ViewShot sends

- **OSC:** `/viewshot/translation` with three strings – translation, original, source (`photo` / `lens`)
- **File:** `~/.local/state/linuxvr-viewshot/translation.json`
  ```json
  {"id": 1791198594286, "time": "2026-10-05T12:34:56",
   "translation": "…", "original": "…", "source": "photo"}
  ```
  A new `id` = a new translation, even with the same text.

OSC is only accepted from this PC (127.0.0.1) – nobody on your network can write into your chatbox.
