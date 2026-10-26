# Follow me

[← Back to the overview](../README.md)

The music moves with you from room to room, using the rooms' occupancy sensors and one Music Assistant group player.

Needs the **Central settings** entry with the **follow-me player**: a Music Assistant group player with dynamic members (e.g. *All players*). There is one follow me per Home Assistant installation. Every other Music Assistant group simply plays: it powers the speakers of its members like direct playback.

> Group players with **static** members are not supported: Music Assistant stops the whole group when a static member is removed. Use groups with dynamic members only.

**Start** follow me in a room where music plays directly on its music player (not video, not in quiet mode), with the room's **Follow me** button or the action `better_media_player.start_follow` ([example](automations.md#follow-me)).

The room joins the follow-me player and its queue moves there (`music_assistant.transfer_queue`); the track goes on where it was (if the follow-me player starts it from the beginning, it is set to the position; not for streams without a duration, e.g. radio). The room's volume becomes follow me's volume (its offset taken out). While follow me runs, the **Follow me** buttons are hidden in every room and the **Stop follow me** buttons are shown ([dynamic controls](dashboards.md#dynamic-controls)); pressing *Follow me* while it runs, or *Stop follow me* while it does not, gives an error.

If follow me cannot be started in a room, the error says why. The button's attribute `blocked_by` shows the same reason (`null` while it can be started):

| `blocked_by` | Reason |
|---|---|
| `no_follow_player` | no follow-me player in the central settings |
| `follow_running` | follow me runs already |
| `start_not_follow_room` | the room takes no part (it needs a music player and occupancy sensors) |
| `start_quiet` | the room is in quiet mode |
| `start_video` | the video player is on |
| `start_follow_paused` | the music player is still in the follow-me player, which does not play: resume it, or start the music on the player itself |
| `start_in_group` | the music plays in a group (named in the error): start it on the player itself |
| `start_not_playing` | no music plays on the room's music player |

While it plays:

- **Prewarm:** the speakers of all follow rooms are switched on, so the sound is there as soon as you walk in.
- **Join:** a room with presence joins. Its volume is set first: follow me's volume plus the room's offset (e.g. bedroom −10 percentage points).
- **Leave:** a room without presence leaves after its presence run-down (2 min). When the last room leaves, Music Assistant pauses the group and follow me ends.
- **Never steals:** a room that plays something else (directly or in another group) is left alone.
- **Left out:** rooms with video playing (Kodi; when the video ends the room comes back, and if it was the last room the group resumes, within 3 h), rooms in **quiet** mode (switch per room; leaves at once and cuts the speakers) and rooms turned **off** during the session (until follow me ends).
- **Unknown presence** (all sensors unavailable) changes nothing, so music does not stop because a sensor integration is down.
- **Nobody detected** at the start: the start room stays as long as nobody is detected in any follow room; then the normal rules apply, including the run-down. The same goes for a room you add to the group by hand.
- **Paused by you:** follow me goes on, rooms stay as they are, the prewarmed speakers run down. On resume the rules apply again.

**Stop** follow me with any room's **Stop follow me** button or `better_media_player.stop_follow` (target: a room player). The music goes back to one room: the room you stop it in if it plays along, else the room it was started in (paused music stays paused there); playing music goes on there where it was. All other rooms stop. Stopping while follow me is off gives an error. Follow me also ends by itself when the follow-me player is stopped in Music Assistant.

---

Related: [Wake up with follow me](night-sleep-wake.md#with-follow-me) · [Handover](handover.md) · [Follow state](reference.md#follow-state)
