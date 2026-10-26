# Dashboards

[← Back to the overview](../README.md)

The room on a dashboard, and how its controls show and hide themselves.

## The room on a dashboard

The screenshots are taken from the Home Assistant demo integration: two demo players stand in for Kodi and a Music Assistant player, the plugs are template switches ([how](../scripts/screenshots/README.md)).

**Movie night:** Kodi plays, so the TV, the speakers and the subwoofer have power. The room player shows what is playing and controls it.

![Dashboard while Kodi plays](images/dashboard.png)

**Music only:** after switching the source to music, Kodi is turned off and the TV plug is cut. Speakers and subwoofer stay on for the music.

![Dashboard while music plays](images/dashboard-music.png)

**Room player:** one entity with power, transport, source selection and the media info of the active player.

<img src="images/more-info.png" alt="More-info dialog of the room player" width="420">

**Mobile:**

<img src="images/dashboard-mobile.png" alt="Dashboard on a phone" width="320">

The device page and the settings of a room: [Setup](setup.md#settings-on-the-device).

## Dynamic controls

A room's controls that make no sense right now are hidden (`hidden_by: integration`), so the automatic dashboards only show what can be used. A control is shown at once when it makes sense again and hidden only after the hide delay (default 3 s), so a short stop between two items changes nothing. The room player and the night mode are always shown; settings and diagnostic entities are left alone.

| Control | Shown while |
|---|---|
| All off | the room plays or is on, or a device still has power (run-down, manual mode) |
| Sleep timer, Sleep timer +15 min | the room plays or is on, or a sleep timer runs |
| Sleep timer end | a sleep timer runs |
| Night mode | always (it is set ahead, also while nothing plays) |
| Quiet | always in a room that takes part in follow me (occupancy sensors, a music player, a follow-me player in the central settings); elsewhere while it is on |
| Wake, Snooze, Stop wake | a wake runs |
| Follow me / Stop follow me | it can be started here / follow me runs |
| Volume preset | the room plays or is on |
| Hand over to | something plays that another room can take over |
| central: Follow rooms | follow me runs |

- "Plays or is on": one of its players is used (video player on, music playing or paused, or turned on with a source), or the room is being turned on.
- A hidden control still works through actions and automations; a hidden button gives an error or does nothing.
- A control you hid stays hidden; one you show again stays shown.
- **Central settings:** the switch *Dynamic controls* (default on; off: nothing is hidden, in every room) and the number *Hide delay* (default 3 s) on its device. The follow-me player is optional there. Without central settings (none, disabled), the defaults apply.

## Wake controls only during a wake

The wake's sensor and its buttons *Snooze* and *Stop wake* are hidden while no wake runs; cards that list them explicitly still show them. To show them only during a wake:

```yaml
type: conditional
conditions:
  - condition: state
    entity: sensor.bedroom_wake
    state_not: idle
card:
  type: entities
  entities:
    - button.bedroom_snooze
    - button.bedroom_stop_wake
```

---

Related: [Reference](reference.md) · [Automations](automations.md)
