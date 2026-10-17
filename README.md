# Better Media Player

One smart media player per room for Home Assistant: it combines the room's media
players (e.g. **Kodi** and a **Music Assistant** / sendspin player) with the power plugs
of **TV, speakers and subwoofer** into a single `media_player` entity — in the spirit of
Better Thermostat.

German documentation: [ANLEITUNG.md](ANLEITUNG.md)

## What it does (v0.1)

- **One entity per room** (`media_player.<room>`): power button, source selection
  (video / music), volume and transport controls go to the player that is currently active.
- **Safe power sequence**
  - on: player hardware plug → display → wake the player and wait until it is ready → speakers → subwoofer
  - off: subwoofer → speakers → display → player hardware plug
- **Demand-based power**: the display is powered while the video player is on, the
  speakers while the video player is on *or* music is playing. Shared speakers stay on as
  long as either player needs them. After that, each part runs down (display 10 min,
  speakers 2 min by default).
- **Auto-on**: music that starts on the room player (also as part of a Music Assistant
  group) switches the speakers on by itself.
- **Room off**: leaves a Music Assistant group (`media_player.unjoin`), stops direct
  playback, turns the video player off and powers everything down.
- **Video idle timeout**: Kodi sitting in the menu for 30 min is turned off.
- **Optional standby wait**: with power sensors, the TV plug is only cut after the TV went
  to standby by itself (or after a maximum time).
- **Manual override**: switch a plug by hand and the room stops switching until all plugs
  are off again.
- Capability-aware: players are turned on/off only if they support it; otherwise an
  optional script/button/switch can be used, or the player's hardware can hang on a plug.

## Requirements

- Home Assistant 2025.2 or newer.
- Power devices must start by themselves when they get power (or be switched otherwise).
- Every plug belongs to exactly **one** controller: disable other automations for these plugs.
- Kodi: `turn_on` / `turn_off` only fire Kodi's device triggers. Your Wake-on-LAN and
  suspend automations for these triggers stay in place.

## Installation

HACS → custom repository → `https://github.com/financelurker/ha-better-media-player`
(category *Integration*), or copy `custom_components/better_media_player` to
`/config/custom_components/`. Restart, then add **Better Media Player** once per room.

## Entities per room

| Entity | Purpose |
|---|---|
| `media_player.<room>` | the room player |
| `sensor.<room>_status` | off, starting, on, playing, run-down, waiting for standby, manual, error, automatic off |
| `binary_sensor.<room>_problem` | start failed (reason in attributes) |
| `binary_sensor.<room>_display_needed` / `_speakers_needed` | current demand |
| `switch.<room>_automatic_power` | automatic switching on/off |
| `number.<room>_*_run_down`, `_video_idle_timeout` | times |
| `button.<room>_all_off` | same as turning the room off |

## Roadmap

v0.2: per-group policies (e.g. "follow me" group with occupancy sensors), a central
settings entry, pausing a follow group when nobody is home.
