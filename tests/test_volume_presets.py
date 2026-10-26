"""Volume presets: room volumes by name."""

from __future__ import annotations

import pytest

from homeassistant.exceptions import ServiceValidationError

from custom_components.better_media_player.players.base import groups_of
from custom_components.better_media_player.const import (
    CONF_PRESET_FADE,
    CONF_VOLUME_CONTROL,
    CONF_VOLUME_ENTITY,
    DOMAIN,
    ENTITY_DEFAULTS,
    VOLUME_DEVICE,
)

from .conftest import AVR, FOLLOW, GROUP, KODI, MUSIC, SPEAKERS, setup_room
from .helpers import (
    MUSIC_ONLY,
    NIGHT,
    PRESET,
    ROOM,
    music_at,
    room_volume,
    select_preset,
    start_wake,
    tick,
    vol,
)


@pytest.fixture(autouse=True)
def _jump(monkeypatch):
    """These tests cover what a preset sets: it jumps there (the fade has
    tests of their own, test_volume_preset_fade.py)."""
    monkeypatch.setitem(ENTITY_DEFAULTS, CONF_PRESET_FADE, 0)


async def test_select_offers_the_presets(hass, home, freezer):
    await music_at(hass, home, freezer)
    st = hass.states.get(PRESET)
    assert st.attributes["options"] == ["quiet", "normal", "loud", "party", "manual"]
    assert st.state == "manual"


async def test_preset_sets_the_room_volume(hass, home, freezer):
    await music_at(hass, home, freezer)
    await select_preset(hass, "loud")
    assert vol(hass) == 0.5
    assert room_volume(hass) == 0.5
    assert hass.states.get(PRESET).state == "loud"
    assert hass.states.get(ROOM).attributes["volume_preset"] == "loud"


async def test_other_volume_ends_the_preset(hass, home, freezer):
    await music_at(hass, home, freezer)
    await select_preset(hass, "normal")
    home.music("playing", volume_level=0.35)  # Music Assistant app
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert hass.states.get(PRESET).state == "manual"
    assert room_volume(hass) == 0.35


async def test_choosing_manual_keeps_the_volume(hass, home, freezer):
    await music_at(hass, home, freezer)
    await select_preset(hass, "normal")
    await select_preset(hass, "manual")
    assert hass.states.get(PRESET).state == "manual"
    assert vol(hass) == 0.3


async def test_action_and_not_offered_preset(hass, home, freezer):
    await music_at(hass, home, freezer, preset_party=0)
    assert "party" not in hass.states.get(PRESET).attributes["options"]
    await hass.services.async_call(DOMAIN, "set_volume_preset", {"entity_id": ROOM, "preset": "quiet"}, blocking=True)
    await hass.async_block_till_done()
    assert vol(hass) == 0.15
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "set_volume_preset", {"entity_id": ROOM, "preset": "party"}, blocking=True)


async def test_select_without_presets_offers_only_manual(hass, home):
    await setup_room(hass, preset_quiet=0, preset_normal=0, preset_loud=0, preset_party=0)
    assert hass.states.get(PRESET).attributes["options"] == ["manual"]


# ------------------------------------------------------------ preset numbers


async def _number(hass, name, value):
    await hass.services.async_call(
        "number", "set_value", {"entity_id": f"number.living_room_preset_{name}", "value": value}, blocking=True
    )
    await hass.async_block_till_done()


async def test_preset_values_are_number_entities(hass, home, freezer):
    from homeassistant.const import EntityCategory
    from homeassistant.helpers import entity_registry as er

    await music_at(hass, home, freezer)
    st = hass.states.get("number.living_room_preset_loud")
    assert float(st.state) == 50 and st.attributes["unit_of_measurement"] == "%"
    assert er.async_get(hass).async_get("number.living_room_preset_loud").entity_category is EntityCategory.CONFIG
    await _number(hass, "loud", 60)
    await select_preset(hass, "loud")
    assert vol(hass) == 0.6


async def test_changing_the_chosen_preset_sets_the_volume(hass, home, freezer):
    await music_at(hass, home, freezer)
    await select_preset(hass, "normal")
    await _number(hass, "normal", 35)
    assert vol(hass) == 0.35
    assert hass.states.get(PRESET).state == "normal"


async def test_preset_set_to_zero_is_no_longer_offered(hass, home, freezer):
    await music_at(hass, home, freezer)
    await select_preset(hass, "party")
    await _number(hass, "party", 0)
    st = hass.states.get(PRESET)
    assert "party" not in st.attributes["options"]
    assert st.state == "manual"
    assert vol(hass) == 0.7  # the volume stays
    await _number(hass, "party", 80)
    assert "party" in hass.states.get(PRESET).attributes["options"]
    assert hass.states.get(PRESET).state == "manual", "a preset dropped once is not chosen again by itself"


async def test_a_dropped_preset_does_not_come_back_after_a_reload(hass, home, freezer):
    entry = await music_at(hass, home, freezer)
    await select_preset(hass, "party")
    await _number(hass, "party", 0)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await _number(hass, "party", 90)
    assert hass.states.get(PRESET).state == "manual"
    assert vol(hass) == 0.7


async def test_another_preset_set_to_zero_leaves_the_select(hass, home, freezer):
    await music_at(hass, home, freezer)
    await select_preset(hass, "normal")
    await _number(hass, "party", 0)
    assert hass.states.get(PRESET).attributes["options"] == ["quiet", "normal", "loud", "manual"]
    assert hass.states.get(PRESET).state == "normal"


async def test_number_value_survives_a_reload(hass, home, freezer):
    entry = await music_at(hass, home, freezer)
    await _number(hass, "quiet", 10)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert float(hass.states.get("number.living_room_preset_quiet").state) == 10


# ---------------------------------------------------------- night / groups


async def test_preset_above_the_night_limit_comes_back_after_the_night(hass, home, freezer):
    await music_at(hass, home, freezer)
    await hass.services.async_call("switch", "turn_on", {"entity_id": NIGHT}, blocking=True)
    await hass.async_block_till_done()
    await select_preset(hass, "party")
    assert vol(hass) == 0.3  # limited
    assert hass.states.get(PRESET).state == "party"
    assert room_volume(hass) == 0.7
    await hass.services.async_call("switch", "turn_off", {"entity_id": NIGHT}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 150, step=5)
    assert vol(hass) == 0.7
    assert hass.states.get(PRESET).state == "party"


async def test_preset_in_a_group_is_only_remembered(hass, home, freezer):
    await music_at(hass, home, freezer)
    home.group("playing", [MUSIC], GROUP)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    await select_preset(hass, "party")
    assert vol(hass) == 0.4, "the group decides its members' volume"
    assert room_volume(hass) == 0.7


async def test_preset_on_a_volume_device(hass, home, freezer):
    home.avr("off")
    await setup_room(hass, **{CONF_VOLUME_CONTROL: VOLUME_DEVICE, CONF_VOLUME_ENTITY: AVR})
    await select_preset(hass, "quiet")  # the AVR is off: remembered
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert vol(hass, AVR) == 0.15
    await select_preset(hass, "loud")
    assert vol(hass, AVR) == 0.5
    assert hass.states.get(PRESET).state == "loud"


# ---------------------------------------------------------------- power on


async def test_power_on_preset(hass, home, freezer):
    entry = await setup_room(hass, power_on_preset="normal")
    entry.runtime_data.data["room_volume"] = 0.7  # last night's party
    home.music("playing", volume_level=0.7)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert vol(hass) == 0.3
    assert hass.states.get(PRESET).state == "normal"


async def test_power_on_preset_not_on_a_restart(hass, home, freezer):
    home.music("playing", volume_level=0.6)  # the room is on already when Home Assistant starts
    entry = await setup_room(hass, power_on_preset="quiet")
    await tick(hass, freezer, 3)
    assert vol(hass) == 0.6
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert vol(hass) == 0.6


async def test_power_on_preset_also_for_video(hass, home, freezer):
    entry = await setup_room(hass, power_on_preset="quiet")
    entry.runtime_data.data["room_volume"] = 0.7
    home.kodi_boots = True
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert vol(hass, KODI) == 0.15


# -------------------------------------------------------------------- wake


async def test_wake_volumes_by_preset(hass, home, freezer):
    entry = await setup_room(hass)
    await start_wake(hass, start_volume="quiet", target_volume="loud", fade_minutes=2)
    w = entry.runtime_data.data["wake"]
    assert (w["start_volume"], w["target_volume"]) == (0.15, 0.5)


async def test_wake_with_a_preset_not_offered(hass, home, freezer):
    await setup_room(hass, preset_loud=0)
    with pytest.raises(ServiceValidationError):
        await start_wake(hass, target_volume="loud")


async def test_wake_target_is_the_power_on_preset(hass, home, freezer):
    entry = await setup_room(hass, power_on_preset="normal")
    entry.runtime_data.data["room_volume"] = 0.7
    await start_wake(hass, start_volume=0.05, fade_minutes=2)
    assert entry.runtime_data.data["wake"]["target_volume"] == 0.3


async def test_player_dropout_is_no_power_on(hass, home, freezer):
    entry = await setup_room(hass, power_on_preset="normal", **MUSIC_ONLY)
    home.music("playing", volume_level=0.6)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    home.music("playing", volume_level=0.62)  # a wish
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    home.music("unavailable")  # Music Assistant reconnects
    await hass.async_block_till_done()
    await tick(hass, freezer, 2)
    home.music("playing", volume_level=0.62)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert vol(hass) == 0.62
    assert entry.runtime_data.data["room_volume"] == 0.62


async def test_start_with_the_player_still_unavailable_is_no_power_on(hass, home, freezer):
    home.music("unavailable")
    entry = await setup_room(hass, power_on_preset="quiet", **MUSIC_ONLY)
    entry.runtime_data.data["room_volume"] = 0.6
    await tick(hass, freezer, 3)
    home.music("playing", volume_level=0.6)  # it comes up playing
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert vol(hass) == 0.6


async def test_restart_with_the_room_on_keeps_the_volume(hass, home, freezer):
    home.music("playing", volume_level=0.6)
    entry = await setup_room(hass, power_on_preset="quiet")
    await tick(hass, freezer, 3)
    assert entry.runtime_data.data.get("volume_preset") is None
    assert hass.states.get(PRESET).state == "manual"


async def test_prewarm_is_no_power_on(hass, home, freezer):
    entry = await setup_room(hass, power_on_preset="quiet")
    ctrl = entry.runtime_data
    await tick(hass, freezer, 3)
    ctrl.data["room_volume"] = 0.45
    ctrl.follow.set_prewarm(True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert home.on(SPEAKERS)
    assert ctrl.data["room_volume"] == 0.45


async def test_switching_the_output_keeps_the_preset(hass, home, freezer):
    entry = await setup_room(hass)
    home.music("playing", volume_level=0.4)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    await select_preset(hass, "loud")
    home.avr("off")
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_VOLUME_CONTROL: VOLUME_DEVICE, CONF_VOLUME_ENTITY: AVR}
    )
    await hass.async_block_till_done()
    assert hass.states.get(PRESET).state == "loud"
    assert room_volume(hass) == 0.5


async def test_preset_value_changed_before_the_update_is_taken(hass, home, freezer, hass_storage):
    """An option change the entity had not taken yet comes with the update (migration)."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.better_media_player.const import (
        CONF_ENTRY_TYPE,
        ENTRY_TYPE_ROOM,
    )

    from .conftest import room_options

    hass_storage[f"{DOMAIN}.room1"] = {
        "version": 1, "key": f"{DOMAIN}.room1",
        "data": {"volume_preset": "normal", "room_volume": 0.3, "preset_normal": 30.0, "cfg_preset_normal": 30.0},
    }
    home.music("playing", volume_level=0.3)
    entry = MockConfigEntry(
        domain=DOMAIN, title="Living Room", entry_id="room1", data={CONF_ENTRY_TYPE: ENTRY_TYPE_ROOM},
        options=room_options(preset_normal=40), version=2,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert hass.states.get(PRESET).state == "normal"
    assert vol(hass) == 0.4


async def test_power_on_preset_reaches_a_device_that_is_always_on(hass, home, freezer):
    from .conftest import AMP

    home.amp(-40.0)
    entry = await setup_room(hass, power_on_preset="quiet", **{CONF_VOLUME_CONTROL: VOLUME_DEVICE, CONF_VOLUME_ENTITY: AMP})
    await tick(hass, freezer, 3)
    entry.runtime_data.data["room_volume"] = 0.5
    home.kodi_boots = True
    await hass.services.async_call("media_player", "turn_on", {"entity_id": "media_player.living_room"}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert hass.states.get(AMP).state == "-68.0"  # 15 %


async def test_preset_on_a_device_while_nothing_plays(hass, home, freezer):
    home.avr("on", volume_level=0.4)
    await setup_room(hass, **{CONF_VOLUME_CONTROL: VOLUME_DEVICE, CONF_VOLUME_ENTITY: AVR})
    await tick(hass, freezer, 3)
    await select_preset(hass, "loud")
    assert vol(hass, AVR) == 0.5


async def test_wake_start_volume_by_preset(hass, home, freezer):
    entry = await setup_room(hass)
    await start_wake(hass, start_volume="normal", target_volume=0.6, fade_minutes=2)
    assert entry.runtime_data.data["wake"]["start_volume"] == 0.3


async def _playing_on_its_own_in_an_idle_group(hass, home, freezer):
    entry = await setup_room(hass)
    entry.runtime_data.data["last_source"] = "audio"
    home.music("playing", volume_level=0.33)
    home.group("idle", [MUSIC], GROUP)  # still lists it, plays nothing
    home.set(MUSIC, "playing")
    await hass.async_block_till_done()
    await tick(hass, freezer)
    return entry


async def test_a_preset_is_set(hass, home, freezer):
    await _playing_on_its_own_in_an_idle_group(hass, home, freezer)
    await hass.services.async_call("select", "select_option", {"entity_id": PRESET, "option": "loud"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(MUSIC).attributes["volume_level"] == 0.5


async def test_a_volume_changed_on_the_player_is_learned(hass, home, freezer):
    entry = await _playing_on_its_own_in_an_idle_group(hass, home, freezer)
    await hass.services.async_call("select", "select_option", {"entity_id": PRESET, "option": "quiet"}, blocking=True)
    await hass.async_block_till_done()
    home.music("playing", volume_level=0.42)  # the Music Assistant app
    await hass.async_block_till_done()
    await tick(hass, freezer)
    assert hass.states.get(PRESET).state == "manual"
    assert entry.runtime_data.volume.value == 0.42


async def test_the_rule(hass, home):
    home.group("idle", [MUSIC], GROUP)
    home.music("idle")
    assert groups_of(hass, MUSIC, idle=True) == [GROUP], "joined, the group does not play yet (follow me)"
    home.music("playing")
    assert groups_of(hass, MUSIC, idle=True) == [], "plays on its own"
    home.group("playing", [MUSIC], FOLLOW)
    assert groups_of(hass, MUSIC, idle=True) == [FOLLOW], "plays in the group"
    assert groups_of(hass, MUSIC) == [FOLLOW]
