"""Room volume on an own device (AV receiver, amplifier) instead of the players."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant import config_entries
from homeassistant.components.media_player import MediaPlayerEntityFeature as F
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util

from custom_components.better_media_player.const import (
    CONF_VOLUME_CONTROL,
    CONF_VOLUME_ENTITY,
    DOMAIN,
    VOLUME_DEVICE,
)

from .conftest import AMP, AVR, KODI, MUSIC, SPEAKERS, setup_room

ROOM = "media_player.wohnzimmer"
NIGHT = "switch.wohnzimmer_night_mode"
WAKE = "sensor.wohnzimmer_wake"


async def _tick(hass, freezer, seconds: float, step: float = 1) -> None:
    left = seconds
    while left > 0:
        freezer.tick(timedelta(seconds=min(step, left)))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        left -= step


async def _settle(hass):
    for _ in range(20):
        await asyncio.sleep(0)
        await hass.async_block_till_done()


def _vol(hass, entity_id=AVR):
    return hass.states.get(entity_id).attributes.get("volume_level")


def _sets(home, entity_id):
    return [d.get("volume_level", d.get("value")) for s, e, d in home.played if s in ("volume_set", "set_value") and e == entity_id]


def _room_volume(hass):
    return hass.states.get(ROOM).attributes["room_volume"]


async def _device_room(hass, home, device=AVR, **overrides):
    home.avr("off")
    home.music("idle", volume_level=0.8)
    home.kodi("off", volume_level=1.0)
    return await setup_room(hass, **{CONF_VOLUME_CONTROL: VOLUME_DEVICE, CONF_VOLUME_ENTITY: device, **overrides})


async def _music_playing(hass, home, freezer):
    entry = await _device_room(hass, home)
    home.music("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert home.on(SPEAKERS) and hass.states.get(AVR).state == "on"
    return entry


async def test_volume_goes_to_the_device(hass, home, freezer):
    await _music_playing(hass, home, freezer)
    await hass.services.async_call("media_player", "volume_set", {"entity_id": ROOM, "volume_level": 0.3}, blocking=True)
    await hass.async_block_till_done()
    assert _vol(hass) == 0.3
    assert _room_volume(hass) == 0.3
    assert _sets(home, MUSIC) == [] and _vol(hass, MUSIC) == 0.8, "players keep their level"
    assert hass.states.get(ROOM).attributes["volume_level"] == 0.3


async def test_device_volume_learned_from_its_remote(hass, home, freezer):
    await _music_playing(hass, home, freezer)
    home.avr("on", volume_level=0.22)  # the AVR's own remote
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert _room_volume(hass) == 0.22


async def test_first_power_on_learns_the_device_volume(hass, home, freezer):
    await _music_playing(hass, home, freezer)
    assert _room_volume(hass) == 0.4  # the AVR's own volume (nothing known before)
    assert _sets(home, AVR) == []


async def test_wish_while_off_is_set_when_the_device_is_ready(hass, home, freezer):
    await _device_room(hass, home)
    await hass.services.async_call("media_player", "volume_set", {"entity_id": ROOM, "volume_level": 0.25}, blocking=True)
    await hass.async_block_till_done()
    assert _room_volume(hass) == 0.25  # remembered
    home.music("playing")  # speakers (and the AVR) come on, the AVR at its own 40 %
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert _vol(hass) == 0.25


async def test_device_power_cycle_gets_the_room_volume_back(hass, home, freezer):
    await _music_playing(hass, home, freezer)
    await hass.services.async_call("media_player", "volume_set", {"entity_id": ROOM, "volume_level": 0.3}, blocking=True)
    await hass.async_block_till_done()
    home.avr_memory = 0.7  # e.g. the AVR resets to its power-on volume
    await hass.services.async_call("switch", "turn_off", {"entity_id": SPEAKERS}, blocking=True)
    await hass.async_block_till_done()
    await hass.services.async_call("switch", "turn_on", {"entity_id": SPEAKERS}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert _vol(hass) == 0.3
    assert _room_volume(hass) == 0.3, "the power-on volume was taken as a wish"


async def test_switching_players_leaves_their_volume(hass, home, freezer):
    await _music_playing(hass, home, freezer)
    home.kodi_boots = True
    await hass.services.async_call("media_player", "select_source", {"entity_id": ROOM, "source": "Kodi"}, blocking=True)
    await hass.async_block_till_done()
    home.kodi("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert _sets(home, KODI) == [] and _sets(home, MUSIC) == []


async def test_step_and_mute_go_to_the_device(hass, home, freezer):
    await _music_playing(hass, home, freezer)
    features = hass.states.get(ROOM).attributes["supported_features"]
    assert features & F.VOLUME_MUTE and features & F.VOLUME_STEP and features & F.VOLUME_SET
    await hass.services.async_call("media_player", "volume_up", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert _vol(hass) == 0.45
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
    await _tick(hass, freezer, 3)
    return entry


async def test_number_maps_its_range(hass, home, freezer):
    await _amp_room(hass, home, freezer)
    assert _room_volume(hass) == 0.5  # -40 dB of -80..0
    await hass.services.async_call("media_player", "volume_set", {"entity_id": ROOM, "volume_level": 0.25}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(AMP).state == "-60.0"
    assert not hass.states.get(ROOM).attributes["supported_features"] & F.VOLUME_MUTE  # a number cannot mute


async def test_number_change_learned(hass, home, freezer):
    await _amp_room(hass, home, freezer)
    home.amp(-20.0)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert _room_volume(hass) == 0.75


async def test_coarse_number_own_steps_are_no_wish(hass, home, freezer):
    """0..30 in steps of 1: our set is reported rounded; that is not someone's change."""
    home.set(AMP, "15.0", min=0.0, max=30.0, step=1.0)
    await _device_room(hass, home, device=AMP)
    home.music("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    await hass.services.async_call("media_player", "volume_set", {"entity_id": ROOM, "volume_level": 0.41}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert hass.states.get(AMP).state == "12.0"
    assert _room_volume(hass) == 0.41


# -------------------------------------------------------------- night mode


async def test_night_mode_limits_the_device_not_the_players(hass, home, freezer):
    await _music_playing(hass, home, freezer)
    await hass.services.async_call("switch", "turn_on", {"entity_id": NIGHT}, blocking=True)
    await hass.async_block_till_done()
    assert _vol(hass) == 0.3  # night limit 30 %
    assert _vol(hass, MUSIC) == 0.8
    home.avr("on", volume_level=0.6)  # louder at the AVR: set back
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert _vol(hass) == 0.3


async def test_night_off_fades_the_device_back(hass, home, freezer):
    await _music_playing(hass, home, freezer)  # room volume 0.4
    await hass.services.async_call("switch", "turn_on", {"entity_id": NIGHT}, blocking=True)
    await hass.async_block_till_done()
    await hass.services.async_call("switch", "turn_off", {"entity_id": NIGHT}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 60, step=5)
    assert 0.3 < _vol(hass) < 0.4
    await _tick(hass, freezer, 90, step=5)
    assert _vol(hass) == 0.4


# --------------------------------------------------------------- sleep / wake


async def test_sleep_timer_fades_the_device(hass, home, freezer):
    await _music_playing(hass, home, freezer)
    await hass.services.async_call(
        DOMAIN, "set_sleep_timer", {"entity_id": ROOM, "minutes": 4, "fade_minutes": 3}, blocking=True
    )
    await hass.async_block_till_done()
    await _tick(hass, freezer, 150, step=10)
    assert _vol(hass) < 0.3
    assert _sets(home, MUSIC) == []
    await _tick(hass, freezer, 120, step=10)
    assert not home.on(SPEAKERS) or hass.states.get(ROOM).state == "off"
    home.avr_memory = 0.05  # the AVR comes back at what the fade left
    home.music("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert _vol(hass) == 0.4, "the room volume from before the fade comes back"


async def test_wake_fades_the_device(hass, home, freezer):
    entry = await _device_room(hass, home)
    entry.runtime_data.data["room_volume"] = 0.5
    payload = {"entity_id": ROOM, "media_content_id": "library://radio/1", "media_content_type": "music",
               "start_volume": 0.05, "target_volume": 0.4, "fade_minutes": 2}
    await hass.services.async_call(DOMAIN, "wake", payload, blocking=True)
    await _settle(hass)
    await _tick(hass, freezer, 3)
    assert _vol(hass) == 0.05, "the device starts at the wake's start volume"
    home.music("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 60, step=5)
    assert 0.05 < _vol(hass) < 0.4
    await _tick(hass, freezer, 70, step=5)
    assert _vol(hass) == 0.4
    assert _sets(home, MUSIC) == []


# ------------------------------------------------------------------- setup


async def test_setup_validation(hass, home):
    home.avr("on", volume_level=0.3)
    hass.states.async_set("media_player.no_volume", "on", {"supported_features": int(F.TURN_ON)})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "room"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"name": "Wohnzimmer"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"video_player": KODI, "audio_player": MUSIC, "special": {}}
    )
    base = {"standby": {}, "follow": {}}
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
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"name": "Wohnzimmer"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"video_player": KODI, "special": {}})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_VOLUME_CONTROL: VOLUME_DEVICE, CONF_VOLUME_ENTITY: AVR, "standby": {}, "follow": {}}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY, "no note: the device sets the volume"


# ------------------------------------------------------ groups and follow me


async def _device_house(hass, home):
    from .conftest import FOLLOW, GROUP, KITCHEN_OCC, KITCHEN_SPK, LIVING_OCC, setup_hub, setup_kitchen

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
    await _tick(hass, freezer, 3)
    assert _room_volume(hass) == 0.33, "the device is no group member: its volume is the room's"


async def test_follow_me_at_night_limits_the_device_only(hass, home, freezer):
    from .conftest import FOLLOW, LIVING_OCC, start_follow

    await _device_house(hass, home)
    hass.states.async_set(LIVING_OCC, "on")
    await start_follow(hass, home)  # player at 50 %
    await _tick(hass, freezer, 3)
    assert MUSIC in home.members(FOLLOW)
    await hass.services.async_call("switch", "turn_on", {"entity_id": NIGHT}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    assert _vol(hass) == 0.3
    assert _vol(hass, MUSIC) == 0.5, "with a volume device the player keeps follow me's volume"


async def test_follow_wake_fades_the_device_and_joins_normally(hass, home, freezer):
    from .conftest import FOLLOW

    await _device_house(hass, home)
    payload = {"entity_id": ROOM, "media_content_id": "library://radio/1", "media_content_type": "music",
               "follow": True, "start_volume": 0.05, "target_volume": 0.4, "fade_minutes": 2}
    await hass.services.async_call(DOMAIN, "wake", payload, blocking=True)
    await _settle(hass)
    await _tick(hass, freezer, 3)
    assert MUSIC in home.members(FOLLOW)
    assert _vol(hass, MUSIC) == 0.15, "joins at follow me's volume (default 25 % - 10 offset), not the start volume"
    assert _vol(hass) <= 0.1
    await _tick(hass, freezer, 130, step=5)
    assert _vol(hass) == 0.4
