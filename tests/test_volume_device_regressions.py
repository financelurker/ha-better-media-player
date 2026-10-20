"""Room volume on an own device: regressions from the review and bug injection."""

from __future__ import annotations

from homeassistant.helpers import entity_registry as er

from custom_components.better_media_player.const import (
    CONF_VOLUME_CONTROL,
    CONF_VOLUME_ENTITY,
    DOMAIN,
    VOLUME_DEVICE,
)

from .conftest import AMP, AVR, FOLLOW, KITCHEN, KITCHEN_OCC, LIVING_OCC, MUSIC, SPEAKERS, setup_room
from .test_volume_device import (
    NIGHT,
    ROOM,
    _device_house,
    _device_room,
    _music_playing,
    _room_volume,
    _sets,
    _settle,
    _tick,
    _vol,
)


async def _set_room(hass, volume):
    await hass.services.async_call("media_player", "volume_set", {"entity_id": ROOM, "volume_level": volume}, blocking=True)
    await hass.async_block_till_done()


async def _night(hass, on=True):
    await hass.services.async_call("switch", "turn_on" if on else "turn_off", {"entity_id": NIGHT}, blocking=True)
    await hass.async_block_till_done()


async def _to_device_mode(hass, entry):
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_VOLUME_CONTROL: VOLUME_DEVICE, CONF_VOLUME_ENTITY: AVR}
    )
    await hass.async_block_till_done()


# ------------------------------------------------------------- follow me


async def test_follow_from_a_device_room_keeps_follow_me_volume(hass, home, freezer):
    """The player's fixed level (e.g. 100 %) is no volume: other rooms must not join at it."""
    await _device_house(hass, home)
    hass.states.async_set(LIVING_OCC, "on")
    home.music("playing", volume_level=1.0)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    await _set_room(hass, 0.3)
    await hass.services.async_call(DOMAIN, "start_follow", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    hass.states.async_set(KITCHEN_OCC, "on")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 10, step=2)
    assert KITCHEN in home.members(FOLLOW)
    assert _vol(hass, KITCHEN) == 0.25, "the kitchen joined at the device room's fixed player level"


async def test_player_gets_its_own_level_back_after_group_playback(hass, home, freezer):
    from .conftest import start_follow

    await _device_house(hass, home)
    hass.states.async_set(LIVING_OCC, "on")
    home.music("playing", volume_level=1.0)  # the player's own level
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    home.music("idle")
    await hass.async_block_till_done()
    await start_follow(hass, home)  # the group sets the player to follow me's volume
    await _tick(hass, freezer, 3)
    assert _vol(hass, MUSIC) != 1.0
    await hass.services.async_call(DOMAIN, "stop_follow", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    assert _vol(hass, MUSIC) == 1.0, "after group playback the player stays at the group's volume"


async def test_group_with_only_device_rooms_not_limited_at_night(hass, home, freezer):
    from .conftest import start_follow

    await _device_house(hass, home)
    hass.states.async_set(LIVING_OCC, "on")
    await start_follow(hass, home)
    await _tick(hass, freezer, 3)
    assert home.members(FOLLOW) == [MUSIC]
    home.set(FOLLOW, "playing", volume_level=0.5)
    await hass.async_block_till_done()
    await _night(hass)
    await _tick(hass, freezer, 5)
    assert hass.states.get(FOLLOW).attributes["volume_level"] > 0.3, "the device is limited, not the group"


async def test_follow_wake_with_the_device_already_on(hass, home, freezer):
    await _device_house(hass, home)
    home.music("playing")  # speakers and AVR already on
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    home.music("idle")
    await hass.async_block_till_done()
    payload = {"entity_id": ROOM, "media_content_id": "library://radio/1", "media_content_type": "music",
               "follow": True, "start_volume": 0.05, "target_volume": 0.4, "fade_minutes": 2}
    await hass.services.async_call(DOMAIN, "wake", payload, blocking=True)
    await _settle(hass)
    await _tick(hass, freezer, 3)
    assert _vol(hass) <= 0.1, "the wake starts quietly on the device"


# --------------------------------------------------------- device states


async def test_power_cycle_while_playing_gets_the_room_volume(hass, home, freezer):
    await _music_playing(hass, home, freezer)
    await _set_room(hass, 0.3)
    home.avr("off")  # the AVR switched off by itself (standby), music goes on
    await hass.async_block_till_done()
    home.avr("on", volume_level=0.7)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert _vol(hass) == 0.3
    assert _room_volume(hass) == 0.3


async def test_device_set_never_above_the_night_limit(hass, home, freezer):
    await _music_playing(hass, home, freezer)
    await _night(hass)
    await _set_room(hass, 0.8)
    assert max(_sets(home, AVR)) <= 0.3


async def test_wake_without_fade_device_late(hass, home, freezer):
    entry = await _device_room(hass, home)
    entry.runtime_data.data["room_volume"] = 0.5
    payload = {"entity_id": ROOM, "media_content_id": "library://radio/1", "media_content_type": "music",
               "start_volume": 0.05, "target_volume": 0.4, "fade_minutes": 0}
    await hass.services.async_call(DOMAIN, "wake", payload, blocking=True)
    await _settle(hass)
    home.avr("unavailable")
    await hass.async_block_till_done()
    home.music("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    home.avr("on", volume_level=0.7)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    assert _vol(hass) == 0.4


async def test_wake_fade_device_late(hass, home, freezer):
    await _device_room(hass, home)
    payload = {"entity_id": ROOM, "media_content_id": "library://radio/1", "media_content_type": "music",
               "start_volume": 0.05, "target_volume": 0.4, "fade_minutes": 2}
    await hass.services.async_call(DOMAIN, "wake", payload, blocking=True)
    await _settle(hass)
    home.avr("unavailable")
    await hass.async_block_till_done()
    home.music("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 30, step=5)
    home.avr("on", volume_level=0.7)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    assert _vol(hass) < 0.3
    await _tick(hass, freezer, 120, step=5)
    assert _vol(hass) == 0.4


# -------------------------------------------------------- coarse devices


async def test_coarse_media_player_night_fade_back(hass, home, freezer):
    home.avr_steps = 30
    await _music_playing(hass, home, freezer)
    await _set_room(hass, 0.6)
    await _tick(hass, freezer, 3)
    await _night(hass)
    await _tick(hass, freezer, 3)
    await _night(hass, False)
    await _tick(hass, freezer, 200, step=5)
    assert abs(_vol(hass) - 0.6) < 0.04
    assert _room_volume(hass) == 0.6


async def test_coarse_media_player_sleep_fade_not_aborted(hass, home, freezer):
    home.avr_steps = 30
    entry = await _music_playing(hass, home, freezer)
    await _set_room(hass, 0.6)
    await _tick(hass, freezer, 3)
    await hass.services.async_call(
        DOMAIN, "set_sleep_timer", {"entity_id": ROOM, "minutes": 4, "fade_minutes": 3}, blocking=True
    )
    await _tick(hass, freezer, 170, step=10)
    assert not entry.runtime_data.data["sleep"].get("fade_aborted")
    assert _room_volume(hass) == 0.6


async def test_stepped_number_holds_the_night_limit(hass, home, freezer):
    home.set(AMP, "15.0", min=0.0, max=30.0, step=1.0)
    await _device_room(hass, home, device=AMP, night_max_volume=25)
    home.music("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    await _night(hass)
    await _set_room(hass, 0.9)
    await _tick(hass, freezer, 3)
    assert float(hass.states.get(AMP).state) <= 7.5, "a stepped number went above the limit"


# ------------------------------------------------------------ mode change


async def test_mode_change_learns_the_room_volume_again(hass, home, freezer):
    home.avr("off")
    home.music("idle", volume_level=0.8)
    entry = await setup_room(hass)
    home.music("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert _room_volume(hass) == 0.8
    await _to_device_mode(hass, entry)
    await _tick(hass, freezer, 3)
    await hass.services.async_call("switch", "turn_off", {"entity_id": SPEAKERS}, blocking=True)
    await hass.async_block_till_done()
    await hass.services.async_call("switch", "turn_on", {"entity_id": SPEAKERS}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert _vol(hass) == 0.4, "the AVR got the players' 80 %"
    assert _room_volume(hass) == 0.4


async def test_mode_change_during_sleep_fade_restores_the_player(hass, home, freezer):
    home.avr("off")
    home.music("idle", volume_level=0.8)
    entry = await setup_room(hass)
    home.music("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    await hass.services.async_call(
        DOMAIN, "set_sleep_timer", {"entity_id": ROOM, "minutes": 4, "fade_minutes": 3}, blocking=True
    )
    await _tick(hass, freezer, 150, step=10)
    low = _vol(hass, MUSIC)
    assert low < 0.6
    await _to_device_mode(hass, entry)
    await _tick(hass, freezer, 200, step=10)
    home.music("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    assert _vol(hass, MUSIC) == 0.8, "the player stays at the faded level for good"


# ------------------------------------------------------------------ setup


async def test_a_room_player_cannot_be_the_volume_device(hass, home):
    from homeassistant import config_entries

    home.group("idle", [], FOLLOW)
    await setup_room(hass)  # media_player.wohnzimmer exists
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


# ------------------------------------------------------- from bug injection


async def test_coarse_number_night_fade_back(hass, home, freezer):
    home.set(AMP, "15.0", min=0.0, max=30.0, step=1.0)
    await _device_room(hass, home, device=AMP)
    home.music("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    await _set_room(hass, 0.6)
    await _tick(hass, freezer, 3)
    await _night(hass)
    await _tick(hass, freezer, 3)
    await _night(hass, False)
    await _tick(hass, freezer, 200, step=5)
    assert hass.states.get(AMP).state == "18.0"
    assert _room_volume(hass) == 0.6


async def test_player_level_changed_on_its_own_is_kept(hass, home, freezer):
    from .conftest import start_follow

    await _device_house(hass, home)
    hass.states.async_set(LIVING_OCC, "on")
    home.music("playing", volume_level=1.0)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    home.music("playing", volume_level=0.9)  # the user sets the player's own level
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    home.music("idle")
    await hass.async_block_till_done()
    await start_follow(hass, home)
    await _tick(hass, freezer, 3)
    await hass.services.async_call(DOMAIN, "stop_follow", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    assert _vol(hass, MUSIC) == 0.9


async def test_follow_wake_is_quiet_before_the_group_plays(hass, home, freezer):
    await _device_house(hass, home)
    home.music("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    home.music("idle")
    await hass.async_block_till_done()
    home.played.clear()
    payload = {"entity_id": ROOM, "media_content_id": "library://radio/1", "media_content_type": "music",
               "follow": True, "start_volume": 0.05, "target_volume": 0.4, "fade_minutes": 2}
    await hass.services.async_call(DOMAIN, "wake", payload, blocking=True)
    await _settle(hass)
    order = [(s, e) for s, e, _d in home.played]
    quiet = order.index(("volume_set", AVR))
    plays = order.index(("play_media", FOLLOW))
    assert quiet < plays, "the group played before the device was set to the start volume"
