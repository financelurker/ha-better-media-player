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

In general: what shows the picture comes on before the player, the sound after it; each from the player on along the [cabling](devices.md) (a receiver before the subwoofer behind it). A device's `on_wait` holds the next one back. Off in the reverse order.

If the player is not ready within the start timeout (90 s), everything switched on is switched off again and the room reports an error.

## When devices go off

| Device | Needed while … | Run-down (default) |
|---|---|---|
| Display | the video player is on (menu, paused, playing) | 10 min |
| Speakers, subwoofer | the video player is on **or** music is playing | 2 min |
| Player hardware | its player is active | 2 min |

Each device has its own run-down (`run_down`, and `night_run_down` at night), so does a player's hardware plug (the player's `run_down`) ([Reference](reference.md#settings)).

Paused music is not demand: the speakers switch off after the run-down and on again when playback resumes. A video player that sits idle in its menu is turned off after 30 min.

**When it goes off:** each switched device (and a player's hardware plug) has a sensor `sensor.<room>_<device>_off_at`, the time its run-down ends (its last need plus its run-down; at night its night run-down). A dashboard shows it as a countdown. If the device still waits after that (for its standby, or for a device that goes off before it), the time stays until it is off. `unknown` while no switching off is planned: needed, off, kept on, automatic power off, manual mode. Automations get the run-down's start, its cancelling and each device switched off as [device triggers](automations.md#run-down).

## Keep on

`switch.<room>_keep_on` keeps what is on on: nothing is switched off automatically – no run-down (devices and a player's hardware), no device off at night, no idle video player turned off, no music player going off with its speakers, nothing an announcement brought up. Starting still switches on what a player needs (unlike *Automatic power* off, which switches nothing at all).

- It stays on until it is turned off or *All off* is pressed (also the room player's off); also over a restart. Turned off, what is on runs down from then on.
- Set it ahead too, e.g. during a film, so nothing goes off afterwards.
- A room off that you did not press yourself (sleep timer, the end of a wake, a handover to another room) stops the players; kept on, the devices stay on. Quiet mode still switches the speakers off: you asked for it.
- The room's status is `kept_on` while devices are on and none is needed.
- Turned on while something is going off: what was not yet switched off stays on (a device already switched off, waiting for its plug, goes on off).
- A start that fails still switches off what it switched on: that was off before.

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
- **TV inputs and AV receiver inputs** are switched by the room once its [cabling](devices.md) is set up (or left to HDMI-CEC); without it, use an automation on the [source change event](automations.md#source-changes).
- **A TV that only an automation can turn on** (e.g. webOS with Wake-on-LAN): keep that automation for its turn-on trigger, as with Kodi.

## Wait for standby

With power sensors and a plug for the device (and its setting `standby` on), a plug is cut only after its device has drawn less than the threshold for 30 s, at most after the maximum wait. Useful for OLED TVs that run panel care in standby. A TV that never goes to standby on its own (no CEC) gains nothing from this; then only the run-down applies.

## Switched by hand

- **A plug switched by hand**, or a device turned on by hand while nothing needs it (e.g. live TV): **manual mode**. The room switches nothing by itself until everything is off again, or until you turn the room player on or off (that also ends it).
- **A device turned off by hand** while it is needed (e.g. the TV with its own remote): it stays off until the next start; everything else goes on automatically.
- **A device turned on by hand while it is needed** (e.g. woken by another device over HDMI-CEC) simply counts as on.
- **Automatic power** (switch on the device) off: no automatic switching at all; commands to the room player still work. Switching it back on ends manual mode too.

---

Related: [Setup](setup.md) · [Room volume](volume.md) · [Players of other systems](players.md)
