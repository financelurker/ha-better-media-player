# Automations

[← Back to the overview](../README.md)

Actions, events and device triggers of Better Media Player for your own automations and scripts, with examples.

## Actions

All of them target a room player (`media_player.<room>`).

| Action | What it does |
|---|---|
| `better_media_player.set_volume_preset` | choose a [volume preset](volume.md#presets) (`preset`, optional `fade_seconds`) |
| `better_media_player.set_sleep_timer` / `cancel_sleep_timer` | [sleep timer](night-sleep-wake.md#sleep-timer) (`minutes`, optional `fade_minutes`) |
| `better_media_player.wake` | [wake up](night-sleep-wake.md#wake-up) (`media_content_id`, `media_content_type`; optional `start_volume`, `target_volume`, `fade_minutes`, `max_duration_minutes`, `night_mode`, `source`, `follow`) |
| `better_media_player.snooze` / `stop_wake` | snooze (optional `minutes`) / stop a wake |
| `better_media_player.start_follow` / `stop_follow` | [follow me](follow-me.md) |
| `better_media_player.handover` | [handover](handover.md) (`destination`; optional `source`, `keep_source_playing`) |
| `better_media_player.announce` | [announcement](announcements.md) in one or more rooms (`message` or `media_content_id`; optional `tts_entity`, `language`, `volume`, `pre_announce`, `ignore_quiet`) |
| `media_player.play_media` with `extra: {night_mode: true}` | play and switch the [night mode](night-sleep-wake.md#night-mode) along |

## Examples

### Volume preset

```yaml
action: better_media_player.set_volume_preset
target:
  entity_id: media_player.living_room
data:
  preset: loud
  fade_seconds: 5           # optional; default: the room's preset fade, 0 = at once
```

### Sleep timer

```yaml
action: better_media_player.set_sleep_timer
target:
  entity_id: media_player.bedroom
data:
  minutes: 45
  fade_minutes: 10   # optional, default: the room's Sleep timer fade-out (5 min)
```

`better_media_player.cancel_sleep_timer` stops it.

### Wake up

```yaml
action: better_media_player.wake
target:
  entity_id: media_player.bedroom
data:
  media_content_id: library://radio/42
  media_content_type: music
  start_volume: 0.05        # default 0.05; or a preset of the room, e.g. quiet
  target_volume: 0.3        # default: the room volume (power-on preset if the wake turns the room on); or a preset, e.g. normal
  fade_minutes: 10          # default 10
  max_duration_minutes: 60  # optional: then the sleep timer fades out and turns the room off
  night_mode: false         # optional: switch the night mode before
```

### Follow me

```yaml
action: better_media_player.start_follow
target:
  entity_id: media_player.living_room
```

`better_media_player.stop_follow` with the same target stops it.

### Handover

```yaml
action: better_media_player.handover
target:
  entity_id: media_player.living_room
data:
  destination: media_player.kitchen
  source: audio                 # optional: audio or video (default: what plays)
  keep_source_playing: false    # optional: this room plays on (Better Kodi only)
```

### Announcement

```yaml
action: better_media_player.announce
target:
  entity_id:
    - media_player.living_room
    - media_player.kitchen
data:
  message: Dinner is ready      # spoken by Home Assistant's text-to-speech; or media_content_id: a sound
  tts_entity: tts.piper         # optional (default: Home Assistant's); also optional: language
  volume: normal                # optional: 0..1 or a preset of the room
  pre_announce: true            # optional: a chime before it (default: the player's setting)
  ignore_quiet: false           # optional: true for emergencies (also quiet rooms)
```

Or through anything that announces on a media player, e.g. Home Assistant's text-to-speech with the room player as its media player:

```yaml
action: tts.speak
target:
  entity_id: tts.piper
data:
  media_player_entity_id: media_player.living_room
  message: Dinner is ready
```

### Play with night mode

Play and switch the room's night mode along:

```yaml
action: media_player.play_media
target:
  entity_id: media_player.bedroom
data:
  media_content_id: library://radio/42
  media_content_type: music
  extra:
    night_mode: true
```

## Events and device triggers

| Event | When | Device triggers |
|---|---|---|
| `better_media_player_source_changed` | the room's source changed ([below](#source-changes)) | *Source changed*, *Switched to video*, *Switched to music*, *Source off* |
| `better_media_player_night_mode` | night mode, once the devices are up ([details](night-sleep-wake.md#anything-else-at-night)) | *Night mode on (devices up)*, *Night mode off (devices up)* |
| `better_media_player_sleep_timer_finished` | the [sleep timer](night-sleep-wake.md#sleep-timer) ended | |
| `better_media_player_wake_started`, `_wake_failed`, `_wake_finished` | a [wake](night-sleep-wake.md#wake-up) started, failed or ended | |

## Source changes

When the room's source changes, the event `better_media_player_source_changed` is fired, e.g. to switch the TV input by IR. The source is `video` while the video player is on, `audio` while the music player plays (also in a group) or is paused while the room still has power for it, or `null` when no player is active any more. A source chosen through the room player (on, source, play, wake) is the room's source from then on, even before something plays (e.g. turned on with music) and during a snooze, until another source plays or the room goes off.

| Data | |
|---|---|
| `entity_id`, `entry_id`, `room` | the room player and the room |
| `source`, `previous` | `video`, `audio` or `null` |
| `player` | the player of the new source |
| `reason` | `selected` (through the room player: source, on, play, wake), `player` (a player took over by itself, e.g. a film cast to Kodi), `off` |

- Fired once the room is ready for it: after the power-on sequence, so the TV already has power (plus its wait after switching on).
- Only for a stable change: two players at the same time for a moment fire nothing. A pause, new media on the same player, a dropout of the current source's player or a restart of Home Assistant fire nothing either. Another player that is unavailable (e.g. behind its host plug) counts as off.
- The same as **device triggers** in the automation editor: *Source changed*, *Switched to video*, *Switched to music*, *Source off*.

```yaml
trigger:
  - trigger: event
    event_type: better_media_player_source_changed
    event_data:
      room: Living room
      source: video
action:
  - action: remote.send_command
    target:
      entity_id: remote.living_room_ir
    data:
      command: tv_input_hdmi1
```

## Night mode event

The AV receiver's sound mode with the room's [night mode](night-sleep-wake.md#anything-else-at-night):

```yaml
trigger:
  - trigger: event
    event_type: better_media_player_night_mode
    event_data:
      room: Living room
action:
  - action: media_player.select_sound_mode
    target:
      entity_id: media_player.living_room_avr
    data:
      sound_mode: "{{ 'Night' if trigger.event.data.night_mode else 'Movie' }}"
```

---

Related: [Dashboards](dashboards.md) · [Reference](reference.md)
