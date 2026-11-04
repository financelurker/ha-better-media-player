# How the room behaves

[← Back to the overview](../README.md)

What the room does by itself: the power button and sources, the order of the devices, when they go off, devices instead of plugs, waiting for standby and what happens when something is switched by hand.

## Power button and sources

- **On** starts the player set as *Starts with* ([Room & behaviour](designer.md#room--behaviour)), else the last used one (a room that is on already stays with the last used one); the first time after setup that is the (first) video player. The last source survives restarts.
- **Source** selects one of the room's players explicitly (each is a source; a room can have several video and music players). The one before is stopped (a video player off, music leaves its group or is paused).
- **Off** (or **All off**): leave a Music Assistant group with `media_player.unjoin` (if it was the last member, Music Assistant pauses the group), otherwise stop direct playback; turn the video player off; switch all plugs and devices off in order (a player's hardware plug once the player is off – at most a minute – and its `off_wait`). A player that takes a while to stop does not switch the room back on; only new playback does.
- **Volume and transport** go to the player that is currently active.

## Order

| | Sequence |
|---|---|
| **On** | player hardware plug → display → wake the player and wait until it is ready → speakers → subwoofer |
| **Off** | subwoofer → speakers → (the player, when its idle timeout ran out) → display → player hardware plug |

In general: what shows the picture comes on before the player, the sound after it; each from the player on along the [cabling](devices.md) (a receiver before the subwoofer behind it). A device's `on_wait` holds the next one back. Off in the reverse order: the sound first (an amplifier before what feeds it, so nothing pops), the player's hardware plug last.

If the player is not ready within the start timeout (90 s), everything switched on is switched off again and the room reports an error.

## When devices go off

One rule for every player:

| The player … | The room |
|---|---|
| plays | everything it needs stays on |
| does not play – paused, stopped, at the end of the list, in its menu, turned on without playing | everything stays on until the **idle timeout** ends (`idle_timeout`, 30 min; at night `night_idle_timeout`, 5 min; 0 = never). Then everything goes off at once: the sound devices first, then the player (one the room turns off), then the picture, the player's hardware plug last |
| is turned off (by you, in its app, with its remote) | everything it needed goes off at once, in the same order |

- Playing again before the idle timeout ends keeps everything on; the next pause starts it anew.
- A device another player still uses stays on (e.g. the speakers for a video while the music is paused).
- Choosing another source switches off at once what the new one does not need (the display after a video, when music starts).
- Follow me leaving the room (nobody there for its presence time): its speakers go off at once.
- A player the room does not switch off (*Switched on and off by: None*, or music without on/off) stays as it is; only its devices go off.
- The picture waits for the player to go off (at most a minute): a TV that the player turns off over HDMI-CEC goes with it.
- A player's hardware plug waits `off_wait` (2 min) after its player is off, so its system can shut down.
- A dropout of the player (unavailable for a moment) is no stop: it counts as not playing.
- After a restart of Home Assistant a player that does not play holds anew (music only while its speakers are on). Until a player has reported (at most 5 min), what is on stays on.
- Choosing a source starts its player: it holds until it plays, also while it still reports off.

**When it goes off:** each switched device (and a player's hardware plug) has a sensor `sensor.<room>_<device>_off_at`: the end of the idle timeout once nothing plays (a player's plug: plus its `off_wait`). A dashboard shows it as a countdown. If the device still waits after that (for its standby, or for the player), the time stays until it is off. `unknown` while no switching off is planned: it plays, off, no idle timeout, kept on, automatic power off, manual mode. Automations get the start, its cancelling and each device switched off as [device triggers](automations.md#run-down).

## Keep on

`switch.<room>_keep_on` keeps what is on on: nothing is switched off automatically – the idle timeout does not end, no device off at night, no device that nothing needs, no player's hardware plug, nothing an announcement brought up. Starting still switches on what a player needs (unlike *Automatic power* off, which switches nothing at all).

- It stays on until it is turned off or *All off* is pressed (also the room player's off); also over a restart. Turned off, what nothing needs goes off at once; a player that does not play holds again for its idle timeout, from then on.
- Set it ahead too, e.g. during a video, so nothing goes off afterwards.
- A room off that you did not press yourself (sleep timer, the end of a wake, a handover to another room) stops the players; kept on, the devices stay on. Quiet mode still switches the speakers off: you asked for it.
- The room's status is `kept_on` while devices are on and nothing plays.
- Turned on while something is going off: what was not yet switched off stays on (a device already switched off, waiting for its plug, goes on off).
- A start that fails still switches off what it switched on: that was off before.

## Devices instead of plugs

Display, speakers and subwoofer can also be devices that turn themselves on and off, alone or together with plugs (the player hardware stays a plug):

| Kind | Switched with | On when |
|---|---|---|
| Plug (`switch`, `light`, `input_boolean`) | `turn_on` / `turn_off` | `on` |
| Media player (TV, AV receiver, soundbar) | `media_player.turn_on` / `turn_off` | on, idle, playing, paused, buffering |
| Remote (e.g. a Harmony activity) | `remote.turn_on` / `turn_off` | `on` |
| Own on/off actions (script, button, input button, scene, automation, switch) | the on / the off action | what was sent last (no feedback) |

- **Plug and device in one role:** on: the plug, then the device once it is there (at most the start timeout; a device that does not come is left out until the next start). Off: the device first, the plug once the device reports off (at most 60 s), after the standby wait if switched on (only a role with a plug waits for standby).
- **A device that does not follow its commands:** a command without effect is sent again after 30 s; after 3 commands the room gives up and logs a warning (while a device boots behind a plug that just came on, up to the start timeout, its commands do not count). A device not switched on is left off until the next start (the panel shows *left off until the next start*), one not switched off is left on (*not switched off, left on until the next start*; its plug still cuts it). The count starts anew once the device reports the state, when the other direction is wanted, and with the next start.
- **One device in two roles** (e.g. a TV with its own speakers as display and speakers): it goes off only when no role needs it any more.
- **TV inputs and AV receiver inputs** are switched by the room once its [cabling](devices.md) is set up (or left to HDMI-CEC); without it, use an automation on the [source change event](automations.md#source-changes).
- **A TV that only an automation can turn on** (e.g. webOS with Wake-on-LAN): keep that automation for its turn-on trigger, as with Kodi.

## Wait for standby

With power sensors and a plug for the device (and its setting `standby` on), a plug is cut only after its device has drawn less than the threshold for 30 s, at most after the maximum wait. Useful for OLED TVs that run panel care in standby. A TV that never goes to standby on its own (no CEC) gains nothing from this.

## Switched by hand

- **A plug switched by hand**, or a device turned on by hand while nothing needs it (e.g. live TV): **manual mode**. The room switches nothing by itself until everything is off again, or until you turn the room player on or off (that also ends it).
- **A device turned off by hand** while it is needed (e.g. the TV with its own remote): it stays off until the next start; everything else goes on automatically.
- **A device turned on by hand while it is needed** (e.g. woken by another device over HDMI-CEC) simply counts as on.
- **Automatic power** (switch on the device) off: no automatic switching at all; commands to the room player still work. Switching it back on ends manual mode too.

---

Related: [Setup](setup.md) · [Room volume](volume.md) · [Players of other systems](players.md)
