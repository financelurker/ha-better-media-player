# Setup

[← Back to the overview](../README.md)

Set up a room once Better Media Player is [installed](../README.md#installation): what is connected in the setup, how the room behaves on its device.

> **Before you start**
>
> - Every plug and device belongs to exactly one controller. Disable automations, scripts or helpers that switch the same plugs or devices (Settings → Entities → plug → **Related** shows them). Devices on a plug alone must start by themselves when they get power.
> - **Kodi:** Home Assistant cannot switch Kodi on or off by itself: `turn_on` / `turn_off` only fire Kodi's device triggers. Keep your Wake-on-LAN and suspend automations on these triggers; they do the actual work.

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=better_media_player)

Settings → Devices & services → Add integration → **Better Media Player** → **A room**, once per room. For follow me, or to change the [dynamic controls](dashboards.md#dynamic-controls), add **Central settings** once as well.

The setup only asks what is connected:

| Step | What you enter |
|---|---|
| **New room** | Name and area. Kodi (also Better Kodi) and Music Assistant players of the area are pre-selected in the next step. |
| **Media players** | Video player (e.g. Kodi) and/or music player (the room's own player, not a group). *Special cases* (collapsed): on/off actions for a player that cannot turn itself on/off, plugs its hardware hangs on. |
| **Devices and volume** | Display (TV, projector), speakers (one or more: active speakers, amplifier, AVR), subwoofer: plugs and/or [devices](room.md#devices-instead-of-plugs). Where the room's volume is set: on the players, or on an own device. *Own on/off actions* (collapsed): scripts or buttons for a device only they switch. *Wait for standby* (collapsed): power sensors. *Follow me* (collapsed): occupancy sensors; rooms without them do not take part. |

Since Home Assistant 2026.6, new entity ids start with the area's name: a room named like its area gets e.g. `media_player.living_room_living_room` (rename it in the entity's settings if you like). These pages write `<room>` for the room's part.

Everything can be changed later via **Configure**. How the room behaves is set on its device (below).

| Menu | Media players | Devices and volume |
|---|---|---|
| ![Options menu](images/options-menu.png) | ![Media players](images/options-players.png) | ![Devices and volume](images/options-devices.png) |

## Examples

| Room | Media players | Power devices |
|---|---|---|
| TV room | Kodi | TV, speakers |
| Office | Music Assistant player | speakers |
| Living room | Kodi + Music Assistant player | TV, speakers (shared), subwoofer |
| Home cinema | Kodi + Music Assistant player | TV (webOS, as media player; keep its [Wake-on-LAN automation](room.md#devices-instead-of-plugs)), AV receiver on a plug (plug, then the receiver) with the room volume on the receiver, subwoofer by IR scripts (an on and an off action) |

## Settings on the device

Each room is a device (Settings → Devices & services → Better Media Player → the room). Its page shows the status, what is needed right now, automatic power, the *All off* button and the room's controls:

![Device page](images/device.png)

How the room behaves – times, waits, standby, night mode, volume presets and follow me – is set under *Configuration* on that page. These are configuration entities: they take effect at once, can be set by automations (`number.set_value`, `switch.turn_on`, `select.select_option`) and do not show up on auto-generated dashboards. A room only has the ones it can use; to start, you can leave them all at their defaults. All of them, with their entity ids, defaults and when a room has them: [Reference](reference.md#settings).

<details>
<summary>Screenshot: the settings of a room</summary>

<img src="images/device-settings.png" alt="Configuration entities of a room" width="500">

</details>

The **Central settings** device has *Follow me: default volume*, *Dynamic controls* and *Hide delay*; its **Configure** only asks for the follow-me player.

## Repairs

What a room is set up with is checked against Home Assistant:

- **A renamed entity** (new entity id) is followed: the room takes the new id by itself.
- **Settings → Repairs** shows, until the cause is gone:
  - entities a room is set up with that do not exist (any more) or are disabled (an error for a missing player, a warning otherwise; checked from a minute after Home Assistant has started, as some integrations add their entities late);
  - plugs, devices or players that several rooms switch (each belongs to one room; sensors may be shared);
  - a group player as a room's player, or a follow-me player that is no Music Assistant group player.

Fix them under **Configure** of the room (or of the central settings).

---

Related: [How the room behaves](room.md) · [Room volume](volume.md) · [Reference](reference.md)
