# Media browser and search

[← Back to the overview](../README.md)

The room player's media browser bundles the browsers of its players. Only what a player supports is offered.

| Entry | Content |
|---|---|
| **Queue: \<video player\>** | the video player's queue, if it has one (e.g. [Better Kodi](https://github.com/financelurker/ha-better-kodi)) |
| **Queue: \<music player\>** | Music Assistant: the current and the next item (that is all Home Assistant exposes) |
| **\<video player\>** | its browser. While it is off: Home Assistant's media sources (e.g. Jellyfin) and a hint |
| **\<music player\>** | its browser (artists, albums, playlists, radio, …) |

- **Playing now** from the browser switches the room to that player: the other player is stopped and the power sequence runs first. A movie picked while Kodi sleeps turns on the TV, wakes Kodi and then plays. A music player in a group leaves it first, so only this room changes.
- **Music items** open a small folder with **▶ Play now**, **⏭ Play next**, **➕ Add to queue** and **⟲ Replace queue** (if the player supports enqueueing). *Next* and *add* change nothing else in the room.
- **Search** goes to the players that can search (Music Assistant). Voice commands like "play Kid Rock in the living room" go to the room player and power the room.
- Media picked through the room player in an automation's media selector stores an id that only the room player understands.
- Browser labels follow the server language set in Home Assistant.

---

Related: [How the room behaves](room.md) · [Players of other systems](players.md)
