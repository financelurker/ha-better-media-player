# Players of other systems

[← Back to the overview](../README.md)

Which media players a room can use, and what a particular system adds.

Any media player can be the room's video or music player. The room then uses what Home Assistant offers for every media player: on, off, play, volume and browsing; a video player counts as active while it is on (also in its menu) and is browsed only while it is on (Home Assistant's media sources stand in while it is off); a music player counts as active while it plays and can be browsed while it is off (one with an on/off of its own, e.g. Music Assistant's power control: see below); a player in a group is noticed by the group's members, and turning the room off takes it out with `media_player.unjoin`.

What a particular system does on top, or differently, lives in a backend of its own (`backend.py`):

| System | Backend | What it adds |
|---|---|---|
| Kodi | `kodi.py` | nothing beyond the video player's defaults |
| Better Kodi | `kodi.py` | its queue is lifted to the room's root; its own play next / add entries are read; handover (also playing on) |
| Music Assistant | `music_assistant.py` | its group players (not taken as a room's player), the queue in the browser, moving a queue with the position of the current track (follow me, handover) |

**Music Assistant's power control:** the room switches the devices (speakers, subwoofer, plugs). Don't set a plug the room switches as a player's *Power Control* in Music Assistant as well: two systems would switch it. To see on/off in Music Assistant, set *Fake power control*: the room then turns the player on with the room (also before follow me joins it) and off with it. In detail, for any music player with an on/off of its own:

- Turned on (e.g. in Music Assistant), the speakers come up; if nothing plays within the speaker run-down, the speakers go off and the player with them.
- The player goes off with its speakers: once they are off (paused or stopped and run down, also after a restart with the speakers off). Its hardware plug, if any, follows after its own run-down.
- It stays on while it is in a playing or paused group (the group decides) and while its speakers are on or needed (manual mode, a wake's snooze). With automatic power off, or in a room without speakers, it is left alone.

Another system is one more module with a subclass of `VideoBackend` or `MusicBackend`, added to the list in `backend.py` (contributions welcome). Follow me works with Music Assistant only: it needs a group player and a system that can move a queue.

---

Related: [How the room behaves](room.md) · [Follow me](follow-me.md) · [Handover](handover.md)
