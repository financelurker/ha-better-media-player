# Reference

[← Back to the overview](../README.md)

Entities, settings and states of a room. `<room>` is the room's part of the entity id (with Home Assistant 2026.6 or newer it may start with the area's name, see [Setup](setup.md)).

## Entities

| Entity | Description |
|---|---|
| `media_player.<room>` | the room player, with media browser and search |
| `sensor.<room>_status` | see table below |
| `binary_sensor.<room>_problem` | start failed; reason in the attributes |
| `binary_sensor.<room>_display_needed` | a device for the picture (display, …) is needed right now |
| `binary_sensor.<room>_speakers_needed` | a device for the sound (speakers, receiver, subwoofer, …) is needed right now |
| `switch.<room>_automatic_power` | off: no automatic switching; commands to the room player still work |
| `switch.<room>_keep_on` | [keep on](room.md#keep-on): nothing is switched off automatically until it is turned off or *All off* |
| `sensor.<room>_<device>_off_at` | when the device [goes off](room.md#when-devices-go-off) (the end of the idle timeout once nothing plays); `unknown` while none is planned (also for a device the room never switches off), and then hidden ([dynamic controls](dashboards.md#dynamic-controls)). `<device>` is the device's ID (`display`, `speakers`, …; `<player>_host` for a player's hardware plug, e.g. `video_host`, `audio_host`); passive devices have none |
| `button.<room>_all_off` | same as turning the room player off |
| `number.<room>_sleep_timer` | sleep timer: minutes left, 0 = off |
| `button.<room>_sleep_timer_15_min` | sleep timer +15 min (starts one if none runs) |
| `sensor.<room>_sleep_timer_end` | when the sleep timer turns the room off |
| `switch.<room>_quiet` | quiet mode: nothing from outside – no follow me, no announcements |
| `switch.<room>_night_mode` | [night mode](night-sleep-wake.md#night-mode) |
| `sensor.<room>_wake` | wake state; snooze end, end time and volumes in the attributes |
| `button.<room>_snooze`, `button.<room>_stop_wake` | wake controls |
| `select.<room>_volume_preset` | volume preset (the offered presets and manual) |
| `select.<room>_hand_over_to` | [handover](handover.md) to another room |
| `sensor.<room>_follow` | follow state of the room (with occupancy sensors only) |
| `button.<room>_follow_me`, `button.<room>_stop_follow_me` | start / stop follow me |
| `sensor.better_media_player_follow_rooms` | central: rooms in follow me; follow-me player, start room in the attributes |

## Settings

How a room behaves. They are not entities: the panel shows them, the action [`better_media_player.set_setting`](automations.md#settings) changes one and `better_media_player.get_settings` returns them. A change takes effect at once and is kept. A switch-like setting also takes `on` / `off` (e.g. an input_boolean's state). To start, you can leave them all at their defaults.

**The room's** (`entity_id`: the room player):

| Setting | Default | Range | What it is |
|---|---|---|---|
| `idle_timeout` | 30 min | 0–480, 0 = never | [nothing plays](room.md#when-devices-go-off) (paused, stopped, a menu) this long: the room goes off |
| `night_idle_timeout` | 5 min | 0–480, 0 = never | the same at night |
| `start_timeout` | 90 s | 10–600 | a start that takes longer fails |
| `night_max_volume` | 30 % | 0–100, 0 = no limit | [night mode](night-sleep-wake.md#night-mode): maximum volume |
| `night_fade_in` | 2 min | 0–30 | after the night: back up to the room volume |
| `preset_quiet`, `preset_normal`, `preset_loud`, `preset_party` | 15 / 30 / 50 / 70 % | 0–100, 0 = not offered | [volume presets](volume.md#presets) |
| `preset_fade` | 0 s | 0–30, 0 = at once | fade to a preset |
| `power_on_preset` | `none` | `none`, `quiet`, `normal`, `loud`, `party` | the room volume whenever the room comes on |
| `announce_video` | `pause` | `pause`, `overlay`, `lower` | [an announcement](announcements.md) during a film |
| `announce_video_lower` | 50 % | 0–100 | `lower`: the film turned down by |
| `sleep_fade` | 5 min | 0–60 | [sleep timer](night-sleep-wake.md#sleep-timer): fade-out at the end |
| `snooze_minutes` | 9 min | 1–120 | wake: snooze time |
| `presence_run_down` | 2 min | 0–60 | [follow me](follow-me.md): the room leaves after no presence this long |
| `volume_offset` | 0 | −50–50 percentage points | follow me: the room's volume against follow me's level |
| `prewarm` | `true` | `true` / `false` | follow me: speakers on as soon as someone comes |
| `wake_detect_left` | `true` | `true` / `false` | follow wake: leaving the wake room ends the wake |

**A device's** (`entity_id` and `device`: its id – `display`, `speakers`, `subwoofer` for a room set up before – or its name):

| Setting | Default | Range | What it is |
|---|---|---|---|
| `on_wait` | 0 s | 0–120 | wait after it is on before the next device (e.g. speakers that need a moment) |
| `standby` | `false` | `true` / `false` | [wait for standby](room.md#wait-for-standby) before its plug goes off (with power sensors) |
| `standby_w` | display 5 W, others 3 W | 0–200 | at or below: standby |
| `standby_max` | display 20 min, others 5 min | 1–120 | wait for standby at most |
| `night_off` | subwoofer `true`, others `false` | `true` / `false` | off in night mode |

**A player's**, for the plug of its hardware (only a player set up with one; `entity_id` and `device`: `video`, `audio` or the player's name):

| Setting | Default | Range | What it is |
|---|---|---|---|
| `off_wait` | 2 min | 0–120 | after the player is off, the plug stays on this long (its system shuts down) |
| `on_wait` | 0 s | 0–120 | wait after the plug is on before the player is woken |

**Central** (no `entity_id`; needs the central entry):

| Setting | Default | Range | What it is |
|---|---|---|---|
| `default_volume` | 25 % | 1–100 | follow me: the volume when nothing is remembered yet |
| `dynamic_controls` | `true` | `true` / `false` | [dynamic controls](dashboards.md#dynamic-controls): hide what makes no sense right now |
| `hide_delay` | 3 s | 0–300 | dynamic controls: hide after |

What they do: [How the room behaves](room.md), [Room volume](volume.md), [Night mode, sleep timer and wake up](night-sleep-wake.md), [Follow me](follow-me.md), [Announcements](announcements.md), [Dashboards](dashboards.md#dynamic-controls).

### Up to version 1.2

The settings were configuration entities on the room's device (`number.<room>_display_run_down`, `select.<room>_power_on_preset`, …) and on the central device. With the update they are removed; their values are kept as settings (a role's become its device's). The run-downs of display and speakers are gone: one [idle timeout](room.md#when-devices-go-off) for every player instead. Automations or scripts that still use one are listed under **Settings → Repairs** until they are changed to the action:

| Up to 1.2 | Now |
|---|---|
| `number.<room>_display_run_down`, `_speaker_run_down`, `_night_display_run_down` | gone: `idle_timeout`, `night_idle_timeout` |
| `number.<room>_wait_after_display_on`, `_wait_after_speakers_on` | `on_wait` of `display`, `speakers` |
| `switch.<room>_display_wait_for_standby`, `number.<room>_display_standby_below`, `_display_wait_for_standby_at_most` (speakers alike) | `standby`, `standby_w`, `standby_max` of `display` (`speakers`) |
| `number.<room>_player_hardware_run_down`, `_wait_after_player_plug_on` | `off_wait`, `on_wait` of the player with the plug (`video`, `audio`) |
| `number.<room>_video_idle_timeout`, `_night_video_idle_timeout`, `_start_timeout` | `idle_timeout`, `night_idle_timeout`, `start_timeout` |
| `number.<room>_night_maximum_volume`, `_night_fade_back_up` | `night_max_volume`, `night_fade_in` |
| `number.<room>_preset_quiet` … `_preset_party`, `_preset_fade`, `select.<room>_power_on_preset` | `preset_quiet` … `preset_party`, `preset_fade`, `power_on_preset` |
| `select.<room>_announcement_during_video`, `number.<room>_announcement_lower_film_by` | `announce_video`, `announce_video_lower` |
| `number.<room>_sleep_timer_fade_out`, `_snooze_time` | `sleep_fade`, `snooze_minutes` |
| `number.<room>_follow_me_leave_after`, `_follow_me_volume_offset`, `switch.<room>_follow_me_prewarm_speakers`, `switch.<room>_wake_detect_leaving_the_room` | `presence_run_down`, `volume_offset`, `prewarm`, `wake_detect_left` |
| `number.better_media_player_follow_me_default_volume`, `_hide_delay`, `switch.better_media_player_dynamic_controls` | central `default_volume`, `hide_delay`, `dynamic_controls` |

## Status

| Status | Meaning |
|---|---|
| `off` | everything without power |
| `starting` | power-on sequence running |
| `on` | a source is in use but not playing (its idle timeout runs) |
| `playing` | a source is playing |
| `run_down` | plugs or devices still on, no demand (waiting for the player to go off, a player's plug its `off_wait`) |
| `stopping` | waiting for a device to reach standby |
| `manual` | a plug was switched (or a device turned on) by hand; automatic switching paused |
| `error` | start failed |
| `disabled` | automatic power is off |
| `kept_on` | devices on, nothing plays, [keep on](room.md#keep-on) is on |

## Follow state

| Follow state | Meaning |
|---|---|
| `inactive` | follow me is off |
| `following` | the room plays along |
| `waiting` | prewarmed, waiting for presence |
| `override` | kept while nobody is detected (start or manual join without presence) |
| `excluded_quiet` / `excluded_video` / `excluded_off` | left out: quiet mode / video / turned off this session |
| `excluded_busy` | plays something else (directly or in another group) |
| `presence_unknown` | occupancy sensors unavailable; nothing changes |
| `waking` / `snoozed` | wake room of a follow wake / snoozed (out of the group meanwhile) |
| `held_out` | occupied when a follow wake started: joins once its presence went and came again |

---

Related: [Setup](setup.md) · [Automations](automations.md) · [Dashboards](dashboards.md)
