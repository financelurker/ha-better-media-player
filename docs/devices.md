# Cabling and inputs

[← Back to the overview](../README.md)

Optional: tell the room how its players and devices are cabled, and it switches TV and AV receiver to the right inputs for what plays – or leaves that to HDMI-CEC and only waits for it. Without cabling the room works as before: it switches the devices on and off and chooses no inputs.

Set it up in the [panel](designer.md): a cable from a tile's right dot to the tile it plugs into; the panel shows the route of each player.

## Connections

A connection is what you have cabled: *from* a player or device, *to* the device it is plugged into, *carries* picture and/or sound, arriving on an *input*, *chosen by* BMP, CEC or nobody.

| Example | From | To | Carries | Input |
|---|---|---|---|---|
| Kodi on the TV's HDMI 2 | Video player | Display | picture, sound | HDMI 2 |
| Kodi's sound on the receiver | Video player | AV receiver | sound | BD |
| The TV's sound back to the receiver (ARC) | Display | AV receiver | sound | TV/ARC |
| Music on the receiver's network input | Music player | AV receiver | sound | NET |
| The receiver's sub out | AV receiver | Subwoofer | sound | – |
| Music played by the TV's app | Music player | Display | sound | – |

- **The route of a source** follows its connections for each signal it needs (video: picture and sound; music: sound). Devices the connections do not reach are fed directly, as without cabling: a display for the picture, speakers, subwoofer, receiver and amplifier for the sound. Mixers and splitters pass on what reaches them, without being switched.
- **What lies on a source's route is switched on for it.** Music routed through the TV (its app, or ARC) switches the TV on; music straight to the receiver leaves the TV off.
- **One input per device and source:** a film cannot need BD and NET on the same receiver. The panel does not save that, nor connections that run in a circle for one signal.
- Removing a player or a device removes its connections.

## Inputs and switching of a device

In a device's details in the panel:

| Setting | Meaning |
|---|---|
| **Inputs via** | A media player with a list of sources (`select_source`), usually the device itself. A cable to the device then offers its inputs. |
| **Inputs by action** | For devices without a source list (IR): one action per input, e.g. `HDMI 1` → `script.tv_hdmi1`. |
| **Pause after choosing an input** | Before the next command (IR devices may need a moment). |
| **Switched on by** / **Switched off by** | **BMP** (default): the room switches the device. **CEC**: a player switches it over HDMI-CEC, the room waits for it. **Never**: left alone. Separately for on and off. Plugs and own on/off actions are always switched by the room. |
| **Fall back to BMP**, **Wait for CEC at most** | If what is expected from CEC does not happen within the wait (default 15 s), the room does it itself – or, without the fallback, gives up: a device not switched on is left off until the next start, an input not chosen is left as it is. |

## What the room does

- **The inputs follow the source in use:** the one starting, else the one playing. A film taking over from music puts the receiver on BD; when the film ends and the music still plays, back to NET.
- **An input is chosen once the device is on** (TVs ignore it while starting), checked after 5 s (with the device's `source`) and chosen once more if needed; then the room gives up (logged). A device that does not tell its input (IR) gets the command once.
- **Changed by hand:** an input you change on the device afterwards is kept until the source changes.
- **CEC:** HDMI-CEC lets devices control each other over the HDMI cable. Switching on is fairly uniform (a starting player wakes the TV and selects its input); switching off depends on the devices (a TV turned off often takes the others with it, a player going off often leaves the TV on). Hence the separate modes for on and off. A device switched on over CEC while the room needs it counts as switched by the room.
- **CEC needs a player that does it:** the room waits for CEC only from the moment the starting player is up. When the room comes on without a player starting (an announcement, follow me's prewarm), nobody switches a CEC device on – set the fallback for such devices.
- **Switched by hand or over CEC:** a device set to be switched on by CEC or never, turned on before the room needs it, does not put the room into manual mode.
- **Manual mode** (a plug switched by hand): inputs are left alone too.
- The order of switching on: host plugs and what is for the picture (display, an HDMI switch), then the player, then what is for the sound – each from the player on along the cables (a receiver before the subwoofer on its sub out).

## Supported setups

All setups are tested with simulated devices. **On real devices so far only scenario 0, with Kodi for video and a Sendspin device of Music Assistant for music.** If you run one of the others (or a variant), feedback in the [issues](https://github.com/financelurker/ha-better-media-player/issues) is very welcome.

### Scenario 0: simple devices on plugs

*Tested: in real use (Kodi for video, a Sendspin device for music).*

- **Setup:** display (a TV or monitor without its own control) and speakers / subwoofer on switchable plugs; a video player (Kodi or Better Kodi) and a music player (Music Assistant).
- **Configuration:** only the room's players and devices; no cabling.
- **Behaviour:** a film switches the display, wakes Kodi, then speakers and subwoofer; music switches only speakers and subwoofer. Everything goes off after its run-down. No inputs to choose.

### Scenario A: TV with CEC, AV receiver, Kodi

*Tested: simulated only, not yet on real devices.*

- **Setup:** a TV integrated in Home Assistant (e.g. webOS) and an AV receiver; Kodi wakes the TV over CEC and selects its input, the TV wakes the receiver (ARC); music from Music Assistant on the receiver's network input.
- **Configuration:**
  - Display: the TV (switching itself), switched on by CEC (with fallback: the TV's `turn_on`, i.e. your Wake-on-LAN automation on its turn-on trigger), inputs via the TV.
  - AV receiver: on its plug and switching itself, inputs via the receiver.
  - Cables: video player → display, picture and sound, HDMI 2, chosen by CEC; display → receiver, sound, TV/ARC, chosen by CEC; music player → receiver, sound, NET, chosen by BMP.
- **Behaviour:** starting a film, the room wakes Kodi and waits for the TV and its input over CEC (it does it itself after the timeout); music afterwards puts the receiver on NET.

---

Related: [How the room behaves](room.md) · [Setup](setup.md) · [Room volume](volume.md)
