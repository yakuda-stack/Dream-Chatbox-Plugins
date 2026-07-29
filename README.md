cd ~/Arbeitsfläche/Dream-Chatbox-Plugins && cat << 'EOF' > README.md
# 🧩 OSC Dream Chatbox Plugins

Welcome to the official plugin repository for **[OSC Dream Chatbox](https://github.com/yakuda-stack/OSC-DreamChatbox)**! 

This repository serves as a collection of official plugins, community extensions, and templates to help developers build custom modules for the OSC Dream Chatbox ecosystem.

---

## 📁 Repository Structure

```text
Dream-Chatbox-Plugins/
├── zip/                  # Pre-built, ready-to-install plugin .zip files
├── template/             # Boilerplate example plugin for developers
└── plugins/              # Source code of official plugins
    └── world_stats/      # Live VRChat instance info & clock integration
```

---

## 🛠 Official Plugins

| Plugin | Version | Description | Source |
| :--- | :--- | :--- | :--- |
| **World Stats** | `1.0.0` | Live VRChat info: player count, current world name, instance type, and local clock via log parsing. | [`plugins/world_stats`](./plugins/world_stats) |

---

## 🚀 How to Install Plugins

1. Download the pre-built `.zip` file of the plugin you want from the [`zip/`](./zip) folder or the Releases section.
2. Open **OSC Dream Chatbox**.
3. Navigate to the **Plugins** tab and click **Install Plugin (.zip)**.
4. Select the downloaded `.zip` file and enable it!

---

## 💻 Developer Guide: Creating Your Own Plugin

Want to build a plugin for OSC Dream Chatbox? You can use the [`template/`](./template) directory as a starting point.

### Plugin Anatomy

A plugin consists of a manifest (`plugin.json`) and the main logic file (`main.py`):

1. **`plugin.json`**: Defines the metadata, global placeholders, and user settings.
2. **`main.py`**: Exports `setup(api)`, `teardown()`, `get_values()`, and optional UI hooks.

### Key Capabilities

* **Global Placeholders:** Register placeholders without prefixes by adding them to `global_placeholders` in `plugin.json` (e.g., `{player_in_world}`, `{realtime}`).
* **Isolated Configuration:** Settings are automatically stored under `plugins/<plugin_id>/configs/config.json`.
* **Zero Dependencies:** Keep core logic independent by bundling local helpers (e.g., log parsers) directly inside your plugin directory.

---

## ⚖️ License

The templates and official plugins in this repository are licensed under the **[MIT License](LICENSE)** — feel free to copy, modify, and distribute your own plugins based on these examples without restrictions.

*(Note: The core [OSC Dream Chatbox](https://github.com/yakuda-stack/OSC-DreamChatbox) application is licensed under GPLv3).*
EOF