# Reference

[← Back to the overview](../README.md)

Entities, settings and states of a room.

## Entities

| Entity | Description |
|---|---|
| `media_player.<room>` | the room player, with media browser and search |
| `sensor.<room>_status` | see table below |
| `binary_sensor.<room>_problem` | start failed; reason in the attributes |
| `binary_sensor.<room>_display_needed` | display demand right now |
| `binary_sensor.<room>_speakers_needed` | speaker demand right now |
| `switch.<room>_automatic_power` | off: no automatic switching; commands to the room player still work |
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

How the room behaves, set under *Configuration* on its device ([Settings on the device](setup.md#settings-on-the-device)). A room only has those it can use.

| Setting | Entity | Default | Present |
|---|---|---|---|
| Display run-down, Speaker run-down, Player hardware run-down | `number.<room>_display_run_down`, `_speaker_run_down`, `_player_hardware_run_down` | 10 / 2 / 2 min | with that device (speaker run-down: also with a subwoofer alone) |
| Video idle timeout | `number.<room>_video_idle_timeout` | 30 min | with a video player |
| Start timeout | `number.<room>_start_timeout` | 90 s | always |
| Wait after display on, Wait after speakers on, Wait after player plug on | `number.<room>_wait_after_display_on`, `_wait_after_speakers_on`, `_wait_after_player_plug_on` | 0 s | with that device |
| Display / Speakers: wait for standby | `switch.<room>_display_wait_for_standby`, `switch.<room>_speakers_wait_for_standby` | off | with power sensors and a plug for it |
| Display / Speakers standby below | `number.<room>_display_standby_below`, `_speakers_standby_below` | 5 / 3 W | with power sensors and a plug for it |
| Display / Speakers: wait for standby at most | `number.<room>_display_wait_for_standby_at_most`, `_speakers_wait_for_standby_at_most` | 20 / 5 min | with power sensors and a plug for it |
| Night: maximum volume | `number.<room>_night_maximum_volume` | 30 %, 0 = no limit | always |
| Night: video idle timeout | `number.<room>_night_video_idle_timeout` | 5 min | with a video player |
| Night: display run-down | `number.<room>_night_display_run_down` | 1 min | with a display |
| Night: fade back up | `number.<room>_night_fade_back_up` | 2 min | always |
| Preset: Quiet / Normal / Loud / Party | `number.<room>_preset_quiet`, `_preset_normal`, `_preset_loud`, `_preset_party` | 15 / 30 / 50 / 70 %, 0 = not offered | always |
| Power-on preset | `select.<room>_power_on_preset` | none (or a preset) | always |
| Preset fade | `number.<room>_preset_fade` | 3 s, 0 = at once | always |
| Announcement during video | `select.<room>_announcement_during_video` | pause (or overlay, lower) | with a video and a music player |
| Announcement: lower film by | `number.<room>_announcement_lower_film_by` | 50 % | with a video and a music player |
| Sleep timer fade-out, Snooze time | `number.<room>_sleep_timer_fade_out`, `_snooze_time` | 5 / 9 min | always |
| Follow me: leave after | `number.<room>_follow_me_leave_after` | 2 min | in follow rooms |
| Follow me: volume offset | `number.<room>_follow_me_volume_offset` | 0 percentage points | in follow rooms |
| Follow me: prewarm speakers, Wake: detect leaving the room | `switch.<room>_follow_me_prewarm_speakers`, `switch.<room>_wake_detect_leaving_the_room` | on | in follow rooms |
| Central: Follow me: default volume, Hide delay, Dynamic controls | `number.better_media_player_follow_me_default_volume`, `_hide_delay`, `switch.better_media_player_dynamic_controls` | 25 %, 3 s, on | on the central settings |

What they do: [How the room behaves](room.md), [Room volume](volume.md), [Night mode, sleep timer and wake up](night-sleep-wake.md), [Follow me](follow-me.md), [Announcements](announcements.md), [Dashboards](dashboards.md#dynamic-controls).

## Status

| Status | Meaning |
|---|---|
| `off` | everything without power |
| `starting` | power-on sequence running |
| `on` | a source is active but not playing |
| `playing` | a source is playing |
| `run_down` | plugs or devices still on, no demand |
| `stopping` | waiting for a device to reach standby |
| `manual` | a plug was switched (or a device turned on) by hand; automatic switching paused |
| `error` | start failed |
| `disabled` | automatic power is off |

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
