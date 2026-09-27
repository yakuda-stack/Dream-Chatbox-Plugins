# 🧩 OSC Dream Chatbox Plugins

The official plugins for **[OSC Dream Chatbox](https://github.com/yakuda-stack/OSC-DreamChatbox)** – plus a template to build your own.

Install them from the **Store** tab inside the app (one click, updates included), or from a `.zip` in [`zip/`](./zip).

---

## 📦 Plugins – what is where

Sorted by topic: each plugin covers one area, so you only install what you use.

| Plugin | Version | What it gives you | Main placeholders |
| :--- | :--- | :--- | :--- |
| **[World Stats](./plugins/world_stats)** | `1.7.0` | Everything from **VRChat and your headset**: players, world, instance type, region, capacity, master, session timers, headset/controller/tracker battery, FPS | `{player_in_world}` `{group_world}` `{instance_type}` `{vrc_region}` `{vrc_instance_capacity}` `{vrc_master}` `{world_time}` `{vr_time}` `{hmd_battery}` `{controller_battery}` `{tracker_battery}` `{tracker_lowest_name}` `{fps}` |
| **[Life Stats](./plugins/life_stats)** | `1.0.0` | Everything **about you**: clock & date, time zones, countdown, weather (Open-Meteo, no key), heart rate (Pulsoid / HypeRate), text from files | `{realtime}` `{realdate}` `{realtime_alt}` `{timezone}` `{timer}` `{weather}` `{weather_temp}` `{weather_condition}` `{heartrate}` `{heartrate_avg}` `{file_text}` |
| **[Stream Stats](./plugins/stream_stats)** | `1.2.0` | **Twitch & YouTube**: channel, title, viewers, chat, live marker, followers | `{s_name}` `{s_status}` `{s_viewer}` `{s_chat}` `{twitch_live}` `{twitch_followers}` |
| **[Social Media](./plugins/social_media)** | `1.2.0` | **Discord, TikTok, Spotify, Instagram**: your handles, the Discord voice channel with people / who talks / mute, TikTok numbers *(experimental)* | `{sm_social}` `{sm_discord}` `{sm_channel}` `{discord_count}` `{discord_speaking}` `{discord_mute_state}` `{tiktok_followers}` `{tiktok_viewers}` |
| **[Linux Extras (from VRCOSC)](./plugins/vrcosc_modules)** | `2.0.0` | Linux-only extras from Bluscream's VRCOSC modules – only what the app doesn't have: RAM/VRAM in GB, network, active window, player status, programs, OpenXR, VRCX, Home Assistant, HTTP, IRC … | `{hw_ram}` `{hw_net}` `{net_total_down}` `{net_utilization}` `{hw_window}` `{md_status}` `{xr_vr}` `{vx_friends}` `{ha_all}` … |
| **[OSC Parameter Profiles](./plugins/osc_paramprofiles)** | `1.2.0` | Save your avatar's parameters and load them back with one click | `{osc_paramprofiles_profile}` … |
| **[OSCLeash](./plugins/oscleash)** | `2.3.0` | Runs ZenithVal's OSCLeash from inside the chatbox | `{oscleash_state}` … |
| **[VR Autostart](./plugins/vr_autostart)** | `1.3.0` | Starts your whole VR set from one program | `{vr_autostart_state}` … |

The full list of placeholders is in each plugin's `plugin.json` and in the app (Plugins page → a plugin → *Placeholders*).

> **Moved in World Stats 1.7.0:** the clock (`{realtime}` `{realdate}` `{realday}` `{realtime_alt}`) now lives in **Life Stats**. The names stayed the same and Life Stats takes your clock settings over on its first start – just install it from the store.

### Works with the chatbox converter

The newer placeholders use the names of [chatbox-converter.github.io](https://chatbox-converter.github.io/), so a MagicChatbox or VRCOSC setup converted to DreamChatbox finds its values: `{weather_temp}`, `{heartrate}`, `{timer}`, `{file_text}`, `{vrc_region}`, `{twitch_followers}`, `{discord_count}`, `{net_total_down}` and so on.

---

## 📁 Repository structure

```text
Dream-Chatbox-Plugins/
├── plugins/          # source of every official plugin (the store reads from here)
├── zip/              # ready-to-install .zip per plugin and version
└── template/         # example_template – start here for your own plugin
```

---

## 🚀 Installing

**From the store (recommended):** Plugins → **Store** → pick a plugin → **Install**. Updates show up as a button on the plugin. Search by name, or by tag (`#weather`, the tag dropdown, or click a tag on a tile – app v1.5.8+).

**From a .zip:** download it from [`zip/`](./zip), then Plugins → **Install plugin from .zip**.

---

## 💻 Building your own plugin

Start from [`template/example_template`](./template/example_template). A plugin is a folder with:

1. **`plugin.json`** – name, version, settings, placeholders.
2. **`main.py`** – `setup(api)`, `get_values()` and whichever other hooks you need.

Good to know:

* **Global placeholders** – list names in `global_placeholders` to use them without the `<plugin_id>_` prefix, e.g. `{player_in_world}`.
* **Settings are stored per plugin** in `plugins/<plugin_id>/configs/config.json` – and, since app v1.5.7, per profile as well.
* **`"headless": false`** in `plugin.json` keeps a plugin out of terminal mode (app v1.5.7+).
* **`"tags": ["weather", "clock"]`** in `plugin.json` makes a plugin findable in the store search and tag filter (app v1.5.8+).
* **No extra dependencies** – bundle small helpers inside your plugin folder, stdlib only.

The full API is documented in the app: [`docs/PLUGIN_API.md`](https://github.com/yakuda-stack/OSC-DreamChatbox/blob/main/docs/PLUGIN_API.md).

---

## ⚖️ License

The template and the official plugins are **[MIT](LICENSE)** – copy, change and share your own plugins freely. Some plugins bundle third-party code under its own license (see the plugin folder).

*(The [OSC Dream Chatbox](https://github.com/yakuda-stack/OSC-DreamChatbox) app itself is GPLv3.)*

Built with help from Claude (Anthropic).
