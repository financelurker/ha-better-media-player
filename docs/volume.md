# Room volume

[← Back to the overview](../README.md)

A room has one volume, not one per player. It is the volume last chosen in the room: on the room player, or directly on one of its players (Music Assistant app, Kodi remote, ...). When a player of the room starts to play on its own, it gets that volume: switching from music to a film keeps the volume, and so does starting a film by casting to Kodi.

- Assumption: the room's players are about equally loud at the same volume level.
- Playback started through the room player gets the volume before it plays. Playback started on the player itself is adjusted a moment after it starts (Home Assistant learns about it only then).
- Group playback (Music Assistant groups, follow me) is left alone: there the group decides. A player still joined to a group that does not play right now counts as in the group, unless it plays on its own (an idle group may still list players that play elsewhere, e.g. Music Assistant's *All players*). The room's volume comes back with the next playback of the room on its own.
- What Better Media Player sets itself (sleep timer, follow me's join volume) does not change the room volume.
- Players that cannot set a volume take no part; the setup shows a note for them.
- The room player shows the room volume in the attribute `room_volume`; `volume_level` is the volume of the active player.

## On an own device

With an amplifier, AV receiver or soundbar that has its own volume, choose **Room volume: Own device** and the **Volume device** in *Devices and volume*: a media player that can set a volume, or a `number` / `input_number` (its range counts as 0–100 %, e.g. −80…0 dB).

- The room's volume is the device's. Volume, steps and mute of the room player go there (mute only with a media player); changes on the device itself (its remote, its app) are learned, also during group playback.
- The players keep their own level: set it once yourself (e.g. Kodi at 100 %). Better Media Player does not change it; after group playback (below) a player gets its own level back when it plays on its own again.
- Sleep timer fade, wake fade, night mode limit and the fade back after the night act on the device.
- While the device is off, a volume set on the room player is remembered; the device gets the room's volume as soon as it is up again (e.g. after its plug switched on), whatever it comes back with.
- **Groups and follow me** still set the players' volume (follow me's volume plus the room's offset): there the group decides. The night mode limits the device, not the player in the group. Started in such a room, follow me keeps its last volume (the player's own level says nothing about loudness).
- Switching between players and a device (or to another device) in the options starts the room volume afresh: it is learned from the new output.
- The note about players that cannot set a volume is not shown: the device sets it.

## Presets

Room volumes by name: **quiet**, **normal**, **loud** and **party** (default 15 / 30 / 50 / 70 %; 0 = not offered in the room). Their volumes are number entities on the device (`Preset: Quiet` etc., `number.<room>_preset_quiet`). Changing the chosen preset's volume sets it right away; a preset set to 0 is no longer offered (the volume stays). Choose one with the select `Volume preset` or the action `better_media_player.set_volume_preset` ([example](automations.md#volume-preset)); this works with players and with a volume device alike.

- The preset becomes the room volume at once (the select shows it right away); the volume of what plays **fades** there: *Preset fade* on the device (default 0 = it jumps; slow at the start and at the end), or `fade_seconds` of the action. Changing the volume during the fade stops it there (that is the room volume then, *manual*); choosing another preset fades on from where the volume is. The night mode (its limit holds), the sleep timer, a wake and follow me take over from a fade; turning the room off ends it. A fade cut by a restart of Home Assistant goes on once the player plays again (within 2 minutes), for the rest of the way; a volume outside the fade's range by then is kept instead (changed meanwhile). In a group (Music Assistant groups, follow me) it is only remembered: there the group decides; the room gets it with its next playback on its own (with a volume device: right away on the device).
- It stays chosen until the volume is changed otherwise; then the select shows **manual** (choosing *manual* keeps the volume as it is). The room player shows it in the attribute `volume_preset`.
- **Night mode:** a preset above the limit is limited, stays chosen and comes back when the night ends.
- **Power-on preset** (select on the device, default none; skipped while its volume is 0): the room volume whenever the room comes on (one of its players is used again after everything was off), e.g. not at last night's party volume. Up from the volume the player starts with it fades; down it is set at once (no fade from last night's party volume). Not after a restart of Home Assistant with the room already on, not when a player drops out for a moment, and not for follow me's prewarm.

---

Related: [Night mode](night-sleep-wake.md#night-mode) · [Automations](automations.md) · [Reference](reference.md#settings)
