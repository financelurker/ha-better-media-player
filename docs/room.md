# How the room behaves

[← Back to the overview](../README.md)

What the room does by itself: the power button and sources, the order of the devices, when they go off, devices instead of plugs, waiting for standby and what happens when something is switched by hand.

## Power button and sources

- **On** starts the last used source; the first time after setup that is the video player. The last source survives restarts.
- **Source** selects video or music explicitly. The other source is stopped (Kodi off, music leaves its group or is paused).
- **Off** (or **All off**): leave a Music Assistant group with `media_player.unjoin` (if it was the last member, Music Assistant pauses the group), otherwise stop direct playback; turn the video player off; switch all plugs and devices off in order. A player that takes a while to stop does not switch the room back on; only new playback does.
- **Volume and transport** go to the player that is currently active.

## Order

| | Sequence |
|---|---|
| **On** | player hardware plug → display → wake the player and wait until it is ready → speakers → subwoofer |
| **Off** | subwoofer → speakers → display → player hardware plug |

If the player is not ready within the start timeout (90 s), everything switched on is switched off again and the room reports an error.

## When devices go off

| Device | Needed while … | Run-down (default) |
|---|---|---|
| Display | the video player is on (menu, paused, playing) | 10 min |
| Speakers, subwoofer | the video player is on **or** music is playing | 2 min |
| Player hardware | its player is active | 2 min |

Paused music is not demand: the speakers switch off after the run-down and on again when playback resumes. A video player that sits idle in its menu is turned off after 30 min.

## Devices instead of plugs

Display, speakers and subwoofer can also be devices that turn themselves on and off, alone or together with plugs (the player hardware stays a plug):

| Kind | Switched with | On when |
|---|---|---|
| Plug (`switch`, `light`, `input_boolean`) | `turn_on` / `turn_off` | `on` |
| Media player (TV, AV receiver, soundbar) | `media_player.turn_on` / `turn_off` | on, idle, playing, paused |
| Remote (e.g. a Harmony activity) | `remote.turn_on` / `turn_off` | `on` |
| Own on/off actions (script, button, scene, automation) | the on / the off action | what was sent last (no feedback) |

- **Plug and device in one role:** on: the plug, then the device once it is there (at most the start timeout; a device that does not come is left out until the next start). Off: the device first, the plug once the device reports off (at most 60 s), after the standby wait if switched on (only a role with a plug waits for standby).
- **One device in two roles** (e.g. a TV with its own speakers as display and speakers): it goes off only when no role needs it any more.
- **TV inputs and AV receiver inputs** are not switched by the room: use an automation on the [source change event](automations.md#source-changes).
- **A TV that only an automation can turn on** (e.g. webOS with Wake-on-LAN): keep that automation for its turn-on trigger, as with Kodi.

## Wait for standby

With power sensors and a plug in the role (and the device's switch *Display: wait for standby* / *Speakers: wait for standby* on), a plug is cut only after its device has drawn less than the threshold for 30 s, at most after the maximum wait. Useful for OLED TVs that run panel care in standby. A TV that never goes to standby on its own (no CEC) gains nothing from this; then only the run-down applies.

## Switched by hand

- **A plug switched by hand**, or a device turned on by hand while nothing needs it (e.g. live TV): **manual mode**. The room switches nothing by itself until everything is off again, or until you turn the room player on or off (that also ends it).
- **A device turned off by hand** while it is needed (e.g. the TV with its own remote): it stays off until the next start; everything else goes on automatically.
- **A device turned on by hand while it is needed** (e.g. woken by another device over HDMI-CEC) simply counts as on.
- **Automatic power** (switch on the device) off: no automatic switching at all; commands to the room player still work. Switching it back on ends manual mode too.

---

Related: [Setup](setup.md) · [Room volume](volume.md) · [Players of other systems](players.md)
