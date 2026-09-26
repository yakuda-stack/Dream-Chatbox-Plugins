# Life Stats

The values about **you** for your chatbox line – everything that is not VRChat or your headset (that is [World Stats](../world_stats)).

| Part | Placeholders | Source |
| :--- | :--- | :--- |
| Clock | `{realtime}` `{realdate}` `{realday}` `{realtime_alt}` `{timezone}` | your PC – moved here from World Stats |
| Countdown | `{timer}` | to a time (`22:00`, `2026-12-31 23:59`) or a Start/Stop timer |
| Weather | `{weather}` `{weather_temp}` `{weather_feels_like}` `{weather_condition}` `{weather_emoji}` `{weather_humidity}` `{weather_wind}` | [Open-Meteo](https://open-meteo.com/) – free, no account, no key |
| Heart rate | `{heartrate}` `{heartrate_avg}` `{heartrate_min}` `{heartrate_max}` `{heartrate_trend}` | [Pulsoid](https://pulsoid.net/) token or [HypeRate](https://www.hyperate.io/) ID + API key |
| Files | `{file_text}` `{file_text_2}` `{file_text_3}` | any text file – e.g. a now-playing file for OBS |

Every part is off until you switch it on, and nothing touches the network while weather and heart rate are off.

## Coming from World Stats

The clock used to be part of World Stats. On its first start Life Stats copies your clock settings over (format, time zone, date, second zone). The placeholder names did not change.

## Setup notes

- **Weather:** type a city (`Berlin`, `Springfield, US`) or coordinates (`52.52,13.41`). Updates every 15 minutes by default. The condition text is available in English or German.
- **Pulsoid:** pulsoid.net → Settings → Tokens → create a *manual token* with `data:heart_rate:read` and paste it.
- **HypeRate:** needs the ID from the HypeRate app **and** an API key, which HypeRate hands out on request.
- **Countdown timer:** set the minutes and press *Start*; it keeps running across an app restart.

Placeholder names follow the [chatbox converter](https://chatbox-converter.github.io/), so converted MagicChatbox/VRCOSC setups work.
