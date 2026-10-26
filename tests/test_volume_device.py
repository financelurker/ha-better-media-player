"""Room volume on an own device (AV receiver, amplifier) instead of the players."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.components.media_player import MediaPlayerEntityFeature as F
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from custom_components.better_media_player.const import (
    CONF_VOLUME_CONTROL,
    CONF_VOLUME_ENTITY,
    DOMAIN,
    VOLUME_DEVICE,
)

from .conftest import AMP, AVR, FOLLOW, KITCHEN, KITCHEN_OCC, KODI, LIVING_OCC, MUSIC, SPEAKERS, setup_room
from .helpers import NIGHT, ROOM, room_volume, set_night, settle, tick, vol


def _sets(home, entity_id):
    return [d.get("volume_level", d.get("value")) for s, e, d in home.played if s in ("volume_set", "set_value") and e == entity_id]


async def _device_room(hass, home, device=AVR, **overrides):
    home.avr("off")
    home.music("idle", volume_level=0.8)
    home.kodi("off", volume_level=1.0)
    return await setup_room(hass, **{CONF_VOLUME_CONTROL: VOLUME_DEVICE, CONF_VOLUME_ENTITY: device, **overrides})


async def _music_playing(hass, home, freezer):
    entry = await _device_room(hass, home)
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert home.on(SPEAKERS) and hass.states.get(AVR).state == "on"
    return entry


async def test_volume_goes_to_the_device(hass, home, freezer):
    await _music_playing(hass, home, freezer)
    await hass.services.async_call("media_player", "volume_set", {"entity_id": ROOM, "volume_level": 0.3}, blocking=True)
    await hass.async_block_till_done()
    assert vol(hass, AVR) == 0.3
    assert room_volume(hass) == 0.3
    assert _sets(home, MUSIC) == [] and vol(hass, MUSIC) == 0.8, "players keep their level"
    assert hass.states.get(ROOM).attributes["volume_level"] == 0.3


async def test_device_volume_learned_from_its_remote(hass, home, freezer):
    await _music_playing(hass, home, freezer)
    home.avr("on", volume_level=0.22)  # the AVR's own remote
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert room_volume(hass) == 0.22


async def test_first_power_on_learns_the_device_volume(hass, home, freezer):
    await _music_playing(hass, home, freezer)
    assert room_volume(hass) == 0.4  # the AVR's own volume (nothing known before)
    assert _sets(home, AVR) == []


async def test_wish_while_off_is_set_when_the_device_is_ready(hass, home, freezer):
    await _device_room(hass, home)
    await hass.services.async_call("media_player", "volume_set", {"entity_id": ROOM, "volume_level": 0.25}, blocking=True)
    await hass.async_block_till_done()
    assert room_volume(hass) == 0.25  # remembered
    home.music("playing")  # speakers (and the AVR) come on, the AVR at its own 40 %
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert vol(hass, AVR) == 0.25


async def test_device_power_cycle_gets_the_room_volume_back(hass, home, freezer):
    await _music_playing(hass, home, freezer)
    await hass.services.async_call("media_player", "volume_set", {"entity_id": ROOM, "volume_level": 0.3}, blocking=True)
    await hass.async_block_till_done()
    home.avr_memory = 0.7  # e.g. the AVR resets to its power-on volume
    await hass.services.async_call("switch", "turn_off", {"entity_id": SPEAKERS}, blocking=True)
    await hass.async_block_till_done()
    await hass.services.async_call("switch", "turn_on", {"entity_id": SPEAKERS}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert vol(hass, AVR) == 0.3
    assert room_volume(hass) == 0.3, "the power-on volume was taken as a wish"


async def test_switching_players_leaves_their_volume(hass, home, freezer):
    await _music_playing(hass, home, freezer)
    home.kodi_boots = True
    await hass.services.async_call("media_player", "select_source", {"entity_id": ROOM, "source": "Kodi"}, blocking=True)
    await hass.async_block_till_done()
    home.kodi("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert _sets(home, KODI) == [] and _sets(home, MUSIC) == []


async def test_step_and_mute_go_to_the_device(hass, home, freezer):
    await _music_playing(hass, home, freezer)
    features = hass.states.get(ROOM).attributes["supported_features"]
    assert features & F.VOLUME_MUTE and features & F.VOLUME_STEP and features & F.VOLUME_SET
    await hass.services.async_call("media_player", "volume_up", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert vol(hass, AVR) == 0.45
    await hass.services.async_call("media_player", "volume_mute", {"entity_id": ROOM, "is_volume_muted": True}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(AVR).attributes["is_volume_muted"] is True
    assert hass.states.get(ROOM).attributes["is_volume_muted"] is True
    assert not [c for c in home.calls if c[2] == MUSIC and c[1] in ("volume_up", "volume_mute")]


# ------------------------------------------------------------------ number


async def _amp_room(hass, home, freezer, value=-40.0):
    home.amp(value)
    entry = await _device_room(hass, home, device=AMP)
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    return entry


async def test_number_maps_its_range(hass, home, freezer):
    await _amp_room(hass, home, freezer)
    assert room_volume(hass) == 0.5  # -40 dB of -80..0
    await hass.services.async_call("media_player", "volume_set", {"entity_id": ROOM, "volume_level": 0.25}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(AMP).state == "-60.0"
    assert not hass.states.get(ROOM).attributes["supported_features"] & F.VOLUME_MUTE  # a number cannot mute


async def test_number_change_learned(hass, home, freezer):
    await _amp_room(hass, home, freezer)
    home.amp(-20.0)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert room_volume(hass) == 0.75


async def test_coarse_number_own_steps_are_no_wish(hass, home, freezer):
    """0..30 in steps of 1: our set is reported rounded; that is not someone's change."""
    home.set(AMP, "15.0", min=0.0, max=30.0, step=1.0)
    await _device_room(hass, home, device=AMP)
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    await hass.services.async_call("media_player", "volume_set", {"entity_id": ROOM, "volume_level": 0.41}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert hass.states.get(AMP).state == "12.0"
    assert room_volume(hass) == 0.41


# -------------------------------------------------------------- night mode


async def test_night_mode_limits_the_device_not_the_players(hass, home, freezer):
    await _music_playing(hass, home, freezer)
    await hass.services.async_call("switch", "turn_on", {"entity_id": NIGHT}, blocking=True)
    await hass.async_block_till_done()
    assert vol(hass, AVR) == 0.3  # night limit 30 %
    assert vol(hass, MUSIC) == 0.8
    home.avr("on", volume_level=0.6)  # louder at the AVR: set back
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert vol(hass, AVR) == 0.3


async def test_night_off_fades_the_device_back(hass, home, freezer):
    await _music_playing(hass, home, freezer)  # room volume 0.4
    await hass.services.async_call("switch", "turn_on", {"entity_id": NIGHT}, blocking=True)
    await hass.async_block_till_done()
    await hass.services.async_call("switch", "turn_off", {"entity_id": NIGHT}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 60, step=5)
    assert 0.3 < vol(hass, AVR) < 0.4
    await tick(hass, freezer, 90, step=5)
    assert vol(hass, AVR) == 0.4


# --------------------------------------------------------------- sleep / wake


async def test_sleep_timer_fades_the_device(hass, home, freezer):
    await _music_playing(hass, home, freezer)
    await hass.services.async_call(
        DOMAIN, "set_sleep_timer", {"entity_id": ROOM, "minutes": 4, "fade_minutes": 3}, blocking=True
    )
    await hass.async_block_till_done()
    await tick(hass, freezer, 150, step=10)
    assert vol(hass, AVR) < 0.3
    assert _sets(home, MUSIC) == []
    await tick(hass, freezer, 120, step=10)
    assert not home.on(SPEAKERS) or hass.states.get(ROOM).state == "off"
    home.avr_memory = 0.05  # the AVR comes back at what the fade left
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert vol(hass, AVR) == 0.4, "the room volume from before the fade comes back"


async def test_wake_fades_the_device(hass, home, freezer):
    entry = await _device_room(hass, home)
    entry.runtime_data.data["room_volume"] = 0.5
    payload = {"entity_id": ROOM, "media_content_id": "library://radio/1", "media_content_type": "music",
               "start_volume": 0.05, "target_volume": 0.4, "fade_minutes": 2}
    await hass.services.async_call(DOMAIN, "wake", payload, blocking=True)
    await settle(hass)
    await tick(hass, freezer, 3)
    assert vol(hass, AVR) == 0.05, "the device starts at the wake's start volume"
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 60, step=5)
    assert 0.05 < vol(hass, AVR) < 0.4
    await tick(hass, freezer, 70, step=5)
    assert vol(hass, AVR) == 0.4
    assert _sets(home, MUSIC) == []


# ------------------------------------------------------------------- setup


async def test_setup_validation(hass, home):
    home.avr("on", volume_level=0.3)
    hass.states.async_set("media_player.no_volume", "on", {"supported_features": int(F.TURN_ON)})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "room"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"name": "Living Room"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"video_player": KODI, "audio_player": MUSIC, "special": {}}
    )
    base = {"actions": {}, "standby": {}, "follow": {}}
    for data, error in (
        ({CONF_VOLUME_CONTROL: VOLUME_DEVICE}, "needs_volume_entity"),
        ({CONF_VOLUME_CONTROL: VOLUME_DEVICE, CONF_VOLUME_ENTITY: MUSIC}, "volume_is_player"),
        ({CONF_VOLUME_CONTROL: VOLUME_DEVICE, CONF_VOLUME_ENTITY: "media_player.no_volume"}, "no_volume"),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {**base, **data})
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {CONF_VOLUME_ENTITY: error}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**base, CONF_VOLUME_CONTROL: VOLUME_DEVICE, CONF_VOLUME_ENTITY: AVR}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_no_volume_note_with_a_device(hass, home):
    hass.states.async_set(KODI, "off", {"supported_features": int(F.TURN_ON)})  # Kodi without volume
    home.avr("on", volume_level=0.3)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "room"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"name": "Living Room"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"video_player": KODI, "special": {}})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_VOLUME_CONTROL: VOLUME_DEVICE, CONF_VOLUME_ENTITY: AVR, "actions": {}, "standby": {}, "follow": {}}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY, "no note: the device sets the volume"


async def test_a_room_player_cannot_be_the_volume_device(hass, home):
    from homeassistant import config_entries

    home.group("idle", [], FOLLOW)
    await setup_room(hass)  # media_player.living_room exists
    assert er.async_get(hass).async_get(ROOM).platform == DOMAIN
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "room"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"name": "Kitchen"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"audio_player": KITCHEN, "special": {}})
    sections = {"actions": {}, "standby": {}, "follow": {}}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_VOLUME_CONTROL: VOLUME_DEVICE, CONF_VOLUME_ENTITY: ROOM, **sections}
    )
    assert result["errors"] == {CONF_VOLUME_ENTITY: "volume_is_room"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_VOLUME_CONTROL: VOLUME_DEVICE, CONF_VOLUME_ENTITY: FOLLOW, **sections}
    )
    assert result["errors"] == {CONF_VOLUME_ENTITY: "group_player"}


# ------------------------------------------------------ groups and follow me


async def _device_house(hass, home):
    from .conftest import (
        FOLLOW,
        GROUP,
        KITCHEN_OCC,
        KITCHEN_SPK,
        LIVING_OCC,
        setup_hub,
        setup_kitchen,
    )

    hass.states.async_set(LIVING_OCC, "off")
    hass.states.async_set(KITCHEN_OCC, "off")
    hass.states.async_set(KITCHEN_SPK, "off")
    home.kitchen("idle")
    home.group("idle", [], FOLLOW)
    home.group("idle", [], GROUP)
    await _device_room(hass, home, occupancy=[LIVING_OCC], volume_offset=-10)
    await setup_kitchen(hass)
    await setup_hub(hass)
    return home


async def test_device_change_learned_during_group_playback(hass, home, freezer):
    from .conftest import GROUP

    await _music_playing(hass, home, freezer)
    home.group("playing", [MUSIC], GROUP)
    await hass.async_block_till_done()
    home.avr("on", volume_level=0.33)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert room_volume(hass) == 0.33, "the device is no group member: its volume is the room's"


async def test_follow_me_at_night_limits_the_device_only(hass, home, freezer):
    from .conftest import FOLLOW, LIVING_OCC, start_follow

    await _device_house(hass, home)
    hass.states.async_set(LIVING_OCC, "on")
    await start_follow(hass, home)  # player at 50 %
    await tick(hass, freezer, 3)
    assert MUSIC in home.members(FOLLOW)
    await hass.services.async_call("switch", "turn_on", {"entity_id": NIGHT}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert vol(hass, AVR) == 0.3
    assert vol(hass, MUSIC) == 0.5, "with a volume device the player keeps follow me's volume"


async def test_follow_wake_fades_the_device_and_joins_normally(hass, home, freezer):
    from .conftest import FOLLOW

    await _device_house(hass, home)
    payload = {"entity_id": ROOM, "media_content_id": "library://radio/1", "media_content_type": "music",
               "follow": True, "start_volume": 0.05, "target_volume": 0.4, "fade_minutes": 2}
    await hass.services.async_call(DOMAIN, "wake", payload, blocking=True)
    await settle(hass)
    await tick(hass, freezer, 3)
    assert MUSIC in home.members(FOLLOW)
    assert vol(hass, MUSIC) == 0.15, "joins at follow me's volume (default 25 % - 10 offset), not the start volume"
    assert vol(hass, AVR) <= 0.1
    await tick(hass, freezer, 130, step=5)
    assert vol(hass, AVR) == 0.4


async def _set_room(hass, volume):
    await hass.services.async_call("media_player", "volume_set", {"entity_id": ROOM, "volume_level": volume}, blocking=True)
    await hass.async_block_till_done()


async def _to_device_mode(hass, entry):
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_VOLUME_CONTROL: VOLUME_DEVICE, CONF_VOLUME_ENTITY: AVR}
    )
    await hass.async_block_till_done()


# ----------------------------------------------- follow me with a volume device


async def test_follow_from_a_device_room_keeps_follow_me_volume(hass, home, freezer):
    """The player's fixed level (e.g. 100 %) is no volume: other rooms must not join at it."""
    await _device_house(hass, home)
    hass.states.async_set(LIVING_OCC, "on")
    home.music("playing", volume_level=1.0)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    await _set_room(hass, 0.3)
    await hass.services.async_call(DOMAIN, "start_follow", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    hass.states.async_set(KITCHEN_OCC, "on")
    await hass.async_block_till_done()
    await tick(hass, freezer, 10, step=2)
    assert KITCHEN in home.members(FOLLOW)
    assert vol(hass, KITCHEN) == 0.25, "the kitchen joined at the device room's fixed player level"


async def test_player_gets_its_own_level_back_after_group_playback(hass, home, freezer):
    from .conftest import start_follow

    await _device_house(hass, home)
    hass.states.async_set(LIVING_OCC, "on")
    home.music("playing", volume_level=1.0)  # the player's own level
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    home.music("idle")
    await hass.async_block_till_done()
    await start_follow(hass, home)  # the group sets the player to follow me's volume
    await tick(hass, freezer, 3)
    assert vol(hass, MUSIC) != 1.0
    await hass.services.async_call(DOMAIN, "stop_follow", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert vol(hass, MUSIC) == 1.0, "after group playback the player stays at the group's volume"


async def test_group_with_only_device_rooms_not_limited_at_night(hass, home, freezer):
    from .conftest import start_follow

    await _device_house(hass, home)
    hass.states.async_set(LIVING_OCC, "on")
    await start_follow(hass, home)
    await tick(hass, freezer, 3)
    assert home.members(FOLLOW) == [MUSIC]
    home.set(FOLLOW, "playing", volume_level=0.5)
    await hass.async_block_till_done()
    await set_night(hass)
    await tick(hass, freezer, 5)
    assert hass.states.get(FOLLOW).attributes["volume_level"] > 0.3, "the device is limited, not the group"


async def test_follow_wake_with_the_device_already_on(hass, home, freezer):
    await _device_house(hass, home)
    home.music("playing")  # speakers and AVR already on
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    home.music("idle")
    await hass.async_block_till_done()
    payload = {"entity_id": ROOM, "media_content_id": "library://radio/1", "media_content_type": "music",
               "follow": True, "start_volume": 0.05, "target_volume": 0.4, "fade_minutes": 2}
    await hass.services.async_call(DOMAIN, "wake", payload, blocking=True)
    await settle(hass)
    await tick(hass, freezer, 3)
    assert vol(hass, AVR) <= 0.1, "the wake starts quietly on the device"


# --------------------------------------------------------- device states


async def test_power_cycle_while_playing_gets_the_room_volume(hass, home, freezer):
    await _music_playing(hass, home, freezer)
    await _set_room(hass, 0.3)
    home.avr("off")  # the AVR switched off by itself (standby), music goes on
    await hass.async_block_till_done()
    home.avr("on", volume_level=0.7)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert vol(hass, AVR) == 0.3
    assert room_volume(hass) == 0.3


async def test_device_set_never_above_the_night_limit(hass, home, freezer):
    await _music_playing(hass, home, freezer)
    await set_night(hass)
    await _set_room(hass, 0.8)
    assert max(_sets(home, AVR)) <= 0.3


async def test_wake_without_fade_device_late(hass, home, freezer):
    entry = await _device_room(hass, home)
    entry.runtime_data.data["room_volume"] = 0.5
    payload = {"entity_id": ROOM, "media_content_id": "library://radio/1", "media_content_type": "music",
               "start_volume": 0.05, "target_volume": 0.4, "fade_minutes": 0}
    await hass.services.async_call(DOMAIN, "wake", payload, blocking=True)
    await settle(hass)
    home.avr("unavailable")
    await hass.async_block_till_done()
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    home.avr("on", volume_level=0.7)
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert vol(hass, AVR) == 0.4


async def test_wake_fade_device_late(hass, home, freezer):
    await _device_room(hass, home)
    payload = {"entity_id": ROOM, "media_content_id": "library://radio/1", "media_content_type": "music",
               "start_volume": 0.05, "target_volume": 0.4, "fade_minutes": 2}
    await hass.services.async_call(DOMAIN, "wake", payload, blocking=True)
    await settle(hass)
    home.avr("unavailable")
    await hass.async_block_till_done()
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 30, step=5)
    home.avr("on", volume_level=0.7)
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert vol(hass, AVR) < 0.3
    await tick(hass, freezer, 120, step=5)
    assert vol(hass, AVR) == 0.4


# -------------------------------------------------------- coarse devices


async def test_coarse_media_player_night_fade_back(hass, home, freezer):
    home.avr_steps = 30
    await _music_playing(hass, home, freezer)
    await _set_room(hass, 0.6)
    await tick(hass, freezer, 3)
    await set_night(hass)
    await tick(hass, freezer, 3)
    await set_night(hass, False)
    await tick(hass, freezer, 200, step=5)
    assert abs(vol(hass, AVR) - 0.6) < 0.04
    assert room_volume(hass) == 0.6


async def test_coarse_media_player_sleep_fade_not_aborted(hass, home, freezer):
    home.avr_steps = 30
    entry = await _music_playing(hass, home, freezer)
    await _set_room(hass, 0.6)
    await tick(hass, freezer, 3)
    await hass.services.async_call(
        DOMAIN, "set_sleep_timer", {"entity_id": ROOM, "minutes": 4, "fade_minutes": 3}, blocking=True
    )
    await tick(hass, freezer, 170, step=10)
    assert not entry.runtime_data.data["sleep"].get("fade_aborted")
    assert room_volume(hass) == 0.6


async def test_stepped_number_holds_the_night_limit(hass, home, freezer):
    home.set(AMP, "15.0", min=0.0, max=30.0, step=1.0)
    await _device_room(hass, home, device=AMP, night_max_volume=25)
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    await set_night(hass)
    await _set_room(hass, 0.9)
    await tick(hass, freezer, 3)
    assert float(hass.states.get(AMP).state) <= 7.5, "a stepped number went above the limit"


# ------------------------------------------------------------ mode change


async def test_mode_change_learns_the_room_volume_again(hass, home, freezer):
    home.avr("off")
    home.music("idle", volume_level=0.8)
    entry = await setup_room(hass)
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert room_volume(hass) == 0.8
    await _to_device_mode(hass, entry)
    await tick(hass, freezer, 3)
    await hass.services.async_call("switch", "turn_off", {"entity_id": SPEAKERS}, blocking=True)
    await hass.async_block_till_done()
    await hass.services.async_call("switch", "turn_on", {"entity_id": SPEAKERS}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert vol(hass, AVR) == 0.4, "the AVR got the players' 80 %"
    assert room_volume(hass) == 0.4


async def test_mode_change_during_sleep_fade_restores_the_player(hass, home, freezer):
    home.avr("off")
    home.music("idle", volume_level=0.8)
    entry = await setup_room(hass)
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    await hass.services.async_call(
        DOMAIN, "set_sleep_timer", {"entity_id": ROOM, "minutes": 4, "fade_minutes": 3}, blocking=True
    )
    await tick(hass, freezer, 150, step=10)
    low = vol(hass, MUSIC)
    assert low < 0.6
    await _to_device_mode(hass, entry)
    await tick(hass, freezer, 200, step=10)
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert vol(hass, MUSIC) == 0.8, "the player stays at the faded level for good"


# ------------------------------------- coarse numbers, own levels, follow wake


async def test_coarse_number_night_fade_back(hass, home, freezer):
    home.set(AMP, "15.0", min=0.0, max=30.0, step=1.0)
    await _device_room(hass, home, device=AMP)
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    await _set_room(hass, 0.6)
    await tick(hass, freezer, 3)
    await set_night(hass)
    await tick(hass, freezer, 3)
    await set_night(hass, False)
    await tick(hass, freezer, 200, step=5)
    assert hass.states.get(AMP).state == "18.0"
    assert room_volume(hass) == 0.6


async def test_player_level_changed_on_its_own_is_kept(hass, home, freezer):
    from .conftest import start_follow

    await _device_house(hass, home)
    hass.states.async_set(LIVING_OCC, "on")
    home.music("playing", volume_level=1.0)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    home.music("playing", volume_level=0.9)  # the user sets the player's own level
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    home.music("idle")
    await hass.async_block_till_done()
    await start_follow(hass, home)
    await tick(hass, freezer, 3)
    await hass.services.async_call(DOMAIN, "stop_follow", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert vol(hass, MUSIC) == 0.9


async def test_follow_wake_is_quiet_before_the_group_plays(hass, home, freezer):
    await _device_house(hass, home)
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    home.music("idle")
    await hass.async_block_till_done()
    home.played.clear()
    payload = {"entity_id": ROOM, "media_content_id": "library://radio/1", "media_content_type": "music",
               "follow": True, "start_volume": 0.05, "target_volume": 0.4, "fade_minutes": 2}
    await hass.services.async_call(DOMAIN, "wake", payload, blocking=True)
    await settle(hass)
    order = [(s, e) for s, e, _d in home.played]
    quiet = order.index(("volume_set", AVR))
    plays = order.index(("play_media", FOLLOW))
    assert quiet < plays, "the group played before the device was set to the start volume"
