"""Volume presets: regressions from the review and bug injection."""

from __future__ import annotations

from custom_components.better_media_player.const import (
    CONF_VOLUME_CONTROL,
    CONF_VOLUME_ENTITY,
    DOMAIN,
    VOLUME_DEVICE,
)

from .conftest import AVR, MUSIC, SPEAKERS, setup_room
from .test_volume_presets import SELECT, _room_volume, _select, _tick, _vol, _wake

MUSIC_ONLY = {"video_player": None, "display": None, "speakers": None, "bass": None}


async def test_player_dropout_is_no_power_on(hass, home, freezer):
    entry = await setup_room(hass, power_on_preset="normal", **MUSIC_ONLY)
    home.music("playing", volume_level=0.6)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    home.music("playing", volume_level=0.62)  # a wish
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    home.music("unavailable")  # Music Assistant reconnects
    await hass.async_block_till_done()
    await _tick(hass, freezer, 2)
    home.music("playing", volume_level=0.62)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert _vol(hass) == 0.62
    assert entry.runtime_data.data["room_volume"] == 0.62


async def test_start_with_the_player_still_unavailable_is_no_power_on(hass, home, freezer):
    home.music("unavailable")
    entry = await setup_room(hass, power_on_preset="quiet", **MUSIC_ONLY)
    entry.runtime_data.data["room_volume"] = 0.6
    await _tick(hass, freezer, 3)
    home.music("playing", volume_level=0.6)  # it comes up playing
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert _vol(hass) == 0.6


async def test_restart_with_the_room_on_keeps_the_volume(hass, home, freezer):
    home.music("playing", volume_level=0.6)
    entry = await setup_room(hass, power_on_preset="quiet")
    await _tick(hass, freezer, 3)
    assert entry.runtime_data.data.get("volume_preset") is None
    assert hass.states.get(SELECT).state == "manual"


async def test_prewarm_is_no_power_on(hass, home, freezer):
    entry = await setup_room(hass, power_on_preset="quiet")
    ctrl = entry.runtime_data
    await _tick(hass, freezer, 3)
    ctrl.data["room_volume"] = 0.45
    ctrl.set_prewarm(True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert home.on(SPEAKERS)
    assert ctrl.data["room_volume"] == 0.45


async def test_switching_the_output_keeps_the_preset(hass, home, freezer):
    entry = await setup_room(hass)
    home.music("playing", volume_level=0.4)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    await _select(hass, "loud")
    home.avr("off")
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_VOLUME_CONTROL: VOLUME_DEVICE, CONF_VOLUME_ENTITY: AVR}
    )
    await hass.async_block_till_done()
    assert hass.states.get(SELECT).state == "loud"
    assert _room_volume(hass) == 0.5


async def test_changed_preset_value_is_taken(hass, home, freezer):
    entry = await setup_room(hass)
    home.music("playing", volume_level=0.4)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    await _select(hass, "normal")
    hass.config_entries.async_update_entry(entry, options={**entry.options, "preset_normal": 40})
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert hass.states.get(SELECT).state == "normal"
    assert _vol(hass) == 0.4


async def test_power_on_preset_reaches_a_device_that_is_always_on(hass, home, freezer):
    from .conftest import AMP

    home.amp(-40.0)
    entry = await setup_room(hass, power_on_preset="quiet", **{CONF_VOLUME_CONTROL: VOLUME_DEVICE, CONF_VOLUME_ENTITY: AMP})
    await _tick(hass, freezer, 3)
    entry.runtime_data.data["room_volume"] = 0.5
    home.kodi_boots = True
    await hass.services.async_call("media_player", "turn_on", {"entity_id": "media_player.wohnzimmer"}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    assert hass.states.get(AMP).state == "-68.0"  # 15 %


async def test_preset_on_a_device_while_nothing_plays(hass, home, freezer):
    home.avr("on", volume_level=0.4)
    await setup_room(hass, **{CONF_VOLUME_CONTROL: VOLUME_DEVICE, CONF_VOLUME_ENTITY: AVR})
    await _tick(hass, freezer, 3)
    await _select(hass, "loud")
    assert _vol(hass, AVR) == 0.5


async def test_wake_start_volume_by_preset(hass, home, freezer):
    entry = await setup_room(hass)
    await _wake(hass, start_volume="normal", target_volume=0.6, fade_minutes=2)
    assert entry.runtime_data.data["wake"]["start_volume"] == 0.3
