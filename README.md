# Better Media Player

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories/)
[![GitHub release](https://img.shields.io/github/v/release/financelurker/ha-better-media-player)](https://github.com/financelurker/ha-better-media-player/releases)
[![CI](https://github.com/financelurker/ha-better-media-player/actions/workflows/ci.yml/badge.svg)](https://github.com/financelurker/ha-better-media-player/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/financelurker/ha-better-media-player)](https://github.com/financelurker/ha-better-media-player/blob/main/LICENSE)

**One smart media player per room.** Better Media Player combines a room's media players (e.g. Kodi and a Music Assistant player) and its TV, speakers and subwoofer into a single Home Assistant entity. Press play and the right devices come on in a safe order; when nothing needs them any more, they go off again by themselves.

It is a custom integration in the spirit of Better Thermostat: you tell it what is in the room, it takes care of the rest.

![Dashboard while Kodi plays](https://raw.githubusercontent.com/financelurker/ha-better-media-player/main/docs/images/dashboard.png?v=ce396117c6)

## What it does

- **[Power in the right order](https://github.com/financelurker/ha-better-media-player/blob/main/docs/room.md#order):** press play on Kodi → the TV comes on, Kodi wakes up, then the speakers; speakers never pop and a player is only used once it is ready.
- **Shared speakers:** one set of speakers (plus subwoofer) serves both a video player such as Kodi and a music player such as Music Assistant.
- **[On and off by itself](https://github.com/financelurker/ha-better-media-player/blob/main/docs/room.md#when-devices-go-off):** music that starts on the room's player – also as part of a Music Assistant group – powers the speakers. When nothing plays any more, everything goes off after a while (e.g. the TV 10 min after Kodi is off).
- **[Plugs or devices](https://github.com/financelurker/ha-better-media-player/blob/main/docs/room.md#devices-instead-of-plugs):** TV, speakers and subwoofer can hang on switchable plugs, turn themselves on and off (a TV or AV receiver as media player, a remote), be switched by your own scripts (e.g. IR) – or a mix.
- **[Room off](https://github.com/financelurker/ha-better-media-player/blob/main/docs/room.md#power-button-and-sources):** leaves a Music Assistant group, stops playback, turns the video player off and powers everything down.
- **[One volume per room](https://github.com/financelurker/ha-better-media-player/blob/main/docs/volume.md)**, with presets (quiet, normal, loud, party) – on the players or on an amplifier, AV receiver or soundbar.
- **[Night mode, sleep timer and wake up](https://github.com/financelurker/ha-better-media-player/blob/main/docs/night-sleep-wake.md)** with fades.
- **[Follow me](https://github.com/financelurker/ha-better-media-player/blob/main/docs/follow-me.md):** start it in a room and the music moves with you from room to room, using the rooms' occupancy sensors and one Music Assistant group player.
- **[Handover](https://github.com/financelurker/ha-better-media-player/blob/main/docs/handover.md):** continue the music or the film in another room, where it was.
- **[Announcements](https://github.com/financelurker/ha-better-media-player/blob/main/docs/announcements.md)** in one or more rooms, over what plays there: a room that is off powers up for it and goes off again; a film pauses; at night at most the night mode's limit; quiet rooms are left out.
- **[Media browser and search](https://github.com/financelurker/ha-better-media-player/blob/main/docs/media-browser.md)** across all of the room's players, with their queues on top; picking a movie powers up TV and Kodi first.
- **[Manual override](https://github.com/financelurker/ha-better-media-player/blob/main/docs/room.md#switched-by-hand):** switch a plug (or turn a device on) by hand and the room steps back until everything is off again.
- **[Tidy dashboards](https://github.com/financelurker/ha-better-media-player/blob/main/docs/dashboards.md):** controls that make no sense right now are hidden.
- **[For your own automations](https://github.com/financelurker/ha-better-media-player/blob/main/docs/automations.md):** events for source changes, night mode, sleep timer and wake up; device triggers for source changes and night mode.

## Is it for you?

**It fits if**

- a room has a video player (e.g. Kodi) and/or a music player (e.g. a Music Assistant player);
- TV, speakers or subwoofer hang on switchable plugs (and start by themselves when they get power), turn on and off by themselves (a media player or a remote, e.g. a TV via webOS or CEC, an AV receiver, a Harmony hub), are switched by your own scripts (e.g. IR), or a mix of these;
- an AV receiver or TV switches between the players: with an automation on the [source change event](https://github.com/financelurker/ha-better-media-player/blob/main/docs/automations.md#source-changes);
- the room's volume is set on the players, or on an amplifier, AV receiver or soundbar with its own volume (a media player or a number);
- for follow me: occupancy sensors in the rooms and one Music Assistant group player with dynamic members.

**It does not fit (yet) if**

- several rooms (zones) hang on one device.

## Installation

Requires Home Assistant 2025.2 or newer.

### HACS

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=financelurker&repository=ha-better-media-player&category=integration)

1. Click the button above. It opens the repository in HACS and adds it as a custom repository if needed.
2. Install **Better Media Player** and restart Home Assistant.

Without the button: HACS → ⋮ → **Custom repositories** → add `https://github.com/financelurker/ha-better-media-player` as type *Integration*.

### Manual

1. Copy `custom_components/better_media_player` into `config/custom_components/`.
2. Restart Home Assistant.

## Getting started

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=better_media_player)

1. Read [Before you start](https://github.com/financelurker/ha-better-media-player/blob/main/docs/setup.md): every plug and device belongs to exactly one controller.
2. Settings → Devices & services → Add integration → **Better Media Player** → **A room**, once per room: its media players, its devices and where its volume is set.
3. Use the room player `media_player.<room>` like any media player: press play and the room powers up. How the room behaves (times, night mode, presets, …) is set on its device; the defaults are a good start.
4. For follow me, or to change the dynamic controls, add **Central settings** once as well.

## Documentation

| Page | Content |
|---|---|
| [Setup](https://github.com/financelurker/ha-better-media-player/blob/main/docs/setup.md) | what the setup asks, examples, the settings on the room's device |
| [How the room behaves](https://github.com/financelurker/ha-better-media-player/blob/main/docs/room.md) | power button and sources, order, run-downs, devices instead of plugs, standby, switched by hand |
| [Room volume](https://github.com/financelurker/ha-better-media-player/blob/main/docs/volume.md) | one volume per room, on an own device, presets |
| [Night mode, sleep timer and wake up](https://github.com/financelurker/ha-better-media-player/blob/main/docs/night-sleep-wake.md) | the night's limit, the sleep timer's fade-out, waking with a fade (also with follow me) |
| [Follow me](https://github.com/financelurker/ha-better-media-player/blob/main/docs/follow-me.md) | the music moves with you from room to room |
| [Handover](https://github.com/financelurker/ha-better-media-player/blob/main/docs/handover.md) | the music or the film goes on in another room |
| [Announcements](https://github.com/financelurker/ha-better-media-player/blob/main/docs/announcements.md) | which player announces, films, volume, quiet rooms, several rooms |
| [Media browser and search](https://github.com/financelurker/ha-better-media-player/blob/main/docs/media-browser.md) | the browsers and queues of the room's players |
| [Automations](https://github.com/financelurker/ha-better-media-player/blob/main/docs/automations.md) | actions, events and device triggers, with YAML examples |
| [Dashboards](https://github.com/financelurker/ha-better-media-player/blob/main/docs/dashboards.md) | screenshots, dynamic controls, cards |
| [Reference](https://github.com/financelurker/ha-better-media-player/blob/main/docs/reference.md) | entities, settings, states |
| [Players of other systems](https://github.com/financelurker/ha-better-media-player/blob/main/docs/players.md) | Kodi, Better Kodi, Music Assistant and other players |

## Support

Questions, bugs and ideas: [GitHub issues](https://github.com/financelurker/ha-better-media-player/issues). For a bug, please add the Home Assistant version, what the room is set up with and a debug log (Settings → Devices & services → Better Media Player → ⋮ → **Enable debug logging**, reproduce it, then disable it again to download the log).

## License

Eclipse Public License 2.0, see [LICENSE](https://github.com/financelurker/ha-better-media-player/blob/main/LICENSE).

This project is not affiliated with Home Assistant, the Open Home Foundation, Kodi or Music Assistant.
