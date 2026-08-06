# Social Media

Your handles in the VRChat chatbox: **Discord**, **TikTok**, **Spotify** and
**Instagram** — every network has its own on/off switch, so only what you tick
ever reaches the chatbox.

Discord can do more than a name: in **live mode** it reads the local Discord
app and shows the server and the voice channel you are sitting in right now.

## Placeholders

| Placeholder | What it shows |
| --- | --- |
| `{sm_social}` | one enabled network at a time, rotating on a timer |
| `{sm_discord}` | the Discord entry — your name, or `Server › Voice channel` |
| `{sm_guild}` | the Discord server on its own |
| `{sm_channel}` | the voice channel on its own |
| `{sm_tiktok}` | TikTok handle |
| `{sm_spotify}` | Spotify name |
| `{sm_instagram}` | Instagram handle |
| `{social_media}` | every enabled network at once |

All of them are global, so they work in the All-in-one template, in the Apps
custom strings and in the status texts. Anything switched off stays empty and
is dropped together with its separators — `{sm_tiktok} | {sm_guild}` never
leaves a stray `|` behind.

The rotation exists because the chatbox is 144 characters wide. Use
`{sm_social}` for a short line, the individual placeholders when you want a
fixed layout, and switch *Rotate the networks* off to get all of them at once.

## Discord live mode

Live mode talks to the Discord desktop app through its local IPC socket — the
same one games use for Rich Presence. **Nothing about your Discord activity
leaves your machine**; the only internet request is the one-time login.

Discord restricts the `rpc` scope to the owner of an application, so the app
has to be one of yours. It takes two minutes and needs no bot:

1. Open <https://discord.com/developers/applications> → **New Application**,
   give it any name (e.g. `DreamChatbox`).
2. On the **General Information** page copy the **Application ID** into
   *Application-ID*.
3. Open the **OAuth2** tab, copy the **Client Secret** into *Client-Secret*.
4. Still in the OAuth2 tab, add the redirect `http://localhost` and save.
   It is never opened — Discord only compares the string.
5. Set *What to show* to **Live server / voice channel**.

The Discord client then shows an **Authorize** popup once. After you click it,
the token is cached in `configs/discord_token.json` next to the plugin and
Discord will not ask again. Delete that file to log out.

While you are not in a voice channel — or Discord is closed — the plugin falls
back to the *Discord name* you typed in, so the line never goes empty.

### If nothing shows up

* **"no running Discord found"** — the client is not started, or it is a
  sandboxed build whose socket lives somewhere unusual. Flatpak, Snap and
  Vesktop paths are checked automatically; a native install always works.
* **"Discord did not accept the connection"** — the Application-ID is wrong.
* **"token exchange refused"** — the Client-Secret or the redirect does not
  match the app.
* **Server name missing, channel name there** — the server list is cached for
  ten minutes; it fills in on the next refresh.

## Files

| File | Purpose |
| --- | --- |
| `main.py` | settings, placeholders, rotation — no network, no Qt |
| `worker.py` | background thread that owns the Discord connection |
| `discordrpc.py` | the IPC client: framing, login, voice channel, servers |
| `netutil.py` | stdlib HTTP helper for the one-time token exchange |

Copyright (C) 2026 yakuda — SPDX-License-Identifier: GPL-3.0-or-later
