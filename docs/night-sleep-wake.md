# Night mode, sleep timer and wake up

[← Back to the overview](../README.md)

For the evening and the morning: the night mode keeps the room quiet, the sleep timer turns it off with a fade, a wake turns it on with one.

## Night mode

The switch `Night mode` puts the room into night mode until it is turned off again. Turning it on and off is up to you or to other integrations and automations (e.g. at bedtime and in the morning).

- **No subwoofer:** it stays off, also when follow me prewarms the speakers. Turned on during playback, the subwoofer goes off at once; turned off, it comes back if something plays. With automatic power off or in manual mode, nothing is switched.
- **Video:** still turns the display on, with shorter times: idle timeout 5 min instead of 30, display run-down 1 min (the display still goes after the speakers).
- **Volume limit** (default 30 %, 0 = none) for both players of the room (with a [volume device](volume.md#on-an-own-device): for the device), whoever sets the volume. What Better Media Player sets itself is limited before it is sent; a higher volume set elsewhere (app, remote) is set back as soon as it is reported, so it may be louder for a moment. A wish above the limit counts as the limit for the room volume.
- **Groups:** a room's player in a group is limited like any other. The group itself is limited only when all its players belong to rooms in night mode. When a room leaves the night mode, its player fades back to the group's volume (follow me: the remembered volume plus the room's offset; other groups: the group's volume from before the night); the group volume itself is not raised, as that would raise the players still in night mode too. A limited volume is never remembered as follow me's volume.
- **Afterwards:** the volume fades back to the room volume (default over 2 min); a volume change during the fade stops it. If nothing plays, the room volume is set at once. A restart during the fade continues it.
- **Sleep timer:** turning the night mode on during the sleep timer's fade keeps the fade going, below the limit.
- After a restart, or when the limit is lowered (number), the limit holds at once.
- `media_player.play_media` on the room player accepts `extra: {night_mode: true}` (or `false`) to switch the night mode along with the playback; it is not passed on to the player.

### Anything else at night

Anything else at night (e.g. the AV receiver's night sound mode or dynamic range compression, the TV's picture mode) is up to you, with the event `better_media_player_night_mode`. It comes when the room's devices are up, so they take the command:

| `reason` | When |
|---|---|
| `switched` | the night mode was switched while the room is on (during a power-on sequence: once it is done) |
| `room_on` | the room came on (a source started after none) in night mode: every time, e.g. for a receiver that forgets its night mode with its power |
| `source` | another source started in night mode, e.g. the TV for video after music |

After the night mode was switched off while the room was off, the next `room_on` / `source` tells `night_mode: false` once. Data: `entity_id`, `entry_id`, `room`, `night_mode` (`true` / `false`), `reason`. It comes after the power-on sequence, right after the [source change event](automations.md#source-changes); like that event, not after a restart of Home Assistant with the room already on. Also as **device triggers**: *Night mode on (devices up)*, *Night mode off (devices up)*.

Example: [the AV receiver's sound mode at night](automations.md#night-mode-event).

## Sleep timer

The room turns itself off after a set time, whatever plays (video or music). Over the last minutes the volume of the active player fades out; then the normal *room off* runs and the volume from before the fade is set again, so the next playback does not start at zero. A player that is suspended at the end (e.g. Kodi) gets its volume when it is up again.

- Set it with the number `Sleep timer` (minutes, 0 = off; shows the minutes left), the button `Sleep timer +15 min` or the actions below. Only while the room is on.
- Turning the room off, or the room going off by itself, ends the timer. Cancelling during the fade sets the volume back.
- Changing the volume during the fade stops the fade only: your volume stays, and the timer still ends the room.
- *+15 min* during the fade sets the volume back; the fade starts again before the new end.
- The timer survives a restart of Home Assistant. If it ran out while Home Assistant was down, the room is turned off at the start only if something still plays.
- The event `better_media_player_sleep_timer_finished` (`entity_id`, `entry_id`, `room`, `reason`: `expired`, `cancelled` or `room_off`) is fired when the timer ends.

`better_media_player.cancel_sleep_timer` stops it. Example: [set a sleep timer](automations.md#sleep-timer).

## Wake up

`better_media_player.wake` wakes one room: the room powers up as usual, the playback starts at the start volume and fades in once it really plays. Example: [wake with the radio](automations.md#wake-up).

- **Which player:** media picked through the room player (media selector of an automation) names the player itself, so a film wakes on Kodi and turns the display on. Other media go to the music player, or to `source: video`.
- **No playback** within 60 s, or a failed start: the event `better_media_player_wake_failed` (`reason`: `start_failed` / `not_playing`) is fired so a different source can be tried, and the room goes off again if it was off before. A successful start fires `better_media_player_wake_started`.
- **Snooze** (button, `better_media_player.snooze` with optional `minutes`, default: the room's *Snooze time*, 9 min, or a pause on the player): pauses (or stops, if the player cannot pause), keeps the room powered, then continues at the start volume and fades in; a player that is no longer paused by then starts the media again. Snoozing during the start starts it again from the beginning afterwards. `max_duration` counts from the last start.
- A **volume change** by hand ends only the fade. Switching the night mode off during the wake lets the fade (or the volume) go up to the full target.
- **Already playing:** a wake replaces the playback; it counts as started once the new playback runs (or something plays at the play check).
- A player that goes idle for a moment (between two items) is no stop; only a stop that lasts 10 s ends the wake.
- **Ends** (`better_media_player_wake_finished`, `reason`): `stopped` (button / `better_media_player.stop_wake` / a stop on the player; the playback goes on unless stopped), `max_duration`, `room_off`, `sleep_timer` (a sleep timer set during the wake; the playback goes on), `replaced` (a new wake), `left` (with follow me, below). A wake ends a running sleep timer.
- What the wake sets is not taken as the room volume; the night mode's limit holds.
- It survives a restart: the fade and the snooze go on, a start cut by the restart is tried again (within 5 min).
- The sensor `Wake` shows `idle`, `starting`, `fading`, `playing` or `snoozed`. It and the buttons *Snooze* and *Stop wake* are hidden while no wake runs ([dynamic controls](dashboards.md#dynamic-controls); pressing the buttons then does nothing). Cards that list them explicitly still show them; to show them only during a wake: [a conditional card](dashboards.md#wake-controls-only-during-a-wake).

Waking several rooms with one call wakes each on its own (not in sync).

### With follow me

With `follow: true` the music follows you out of the wake room: the wake starts follow me (or joins it). It needs the central settings, a music player and occupancy sensors in the wake room.

- **The wake room** joins the follow-me player at its start volume and fades in its own player. Only it fades: rooms that join later get follow me's normal volume. Its quiet mode does not keep it out while it wakes.
- **Follow me off or running:** if follow me is off, the wake starts it with the wake's media; players left in the group from before (e.g. paused last night) leave it. Rooms that are occupied at that moment (someone already in the kitchen) stay out until their presence went and came again. If follow me already runs (or was just started by another wake), the wake room joins it and its media goes on.
- If Music Assistant drops the wake room from the group, it is taken back in; a group stopped in the app ends the wake. After a restart with the group stopped, the wake room joins again.
- **Left = woken up:** the wake is done (`wake_finished`, reason `left`) when presence was seen in the wake room after the wake started, the room then ran empty (presence run-down), **and** someone turned up in another follow room — from 2 min before the wake room was last occupied on (the sensor there may switch on first). The room leaves the group like any follow room; the music plays on where you are. A sensor that loses a sleeper for a while (mmWave) is no "left", nor is someone who got up much earlier. As long as no presence was seen, the wake room stays until the wake ends otherwise. The switch *Wake: detect leaving the room* on the room's device switches this off; then a follow wake there ends only by stop, maximum duration, room off or a sleep timer.
- **Snooze** only snoozes the wake room: it leaves the group meanwhile (the others play on; as the only member the group pauses) and comes back at the start volume. Follow me does not take it back in during the snooze.
- **Ends:** `stop_wake` makes it a normal follow room; `max_duration` fades out only the wake room, which then leaves the group; room off takes only this room out.
- Night mode is per room as always: a room in night mode joins without subwoofer and limited.

---

Related: [Room volume](volume.md) · [Follow me](follow-me.md) · [Automations](automations.md)
