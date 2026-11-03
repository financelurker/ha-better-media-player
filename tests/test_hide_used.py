"""The central setting hide_used: the entities the rooms use are hidden in Home Assistant."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryDisabler
from homeassistant.helpers import entity_registry as er

from .conftest import BASS, FOLLOW, KODI, LG, LIVING_OCC, MUSIC, SPEAKERS, TV, setup_hub, setup_room
from .helpers import set_setting, tick

INTEGRATION = er.RegistryEntryHider.INTEGRATION
USER = er.RegistryEntryHider.USER
ALL = (KODI, MUSIC, LG, TV, SPEAKERS, BASS, FOLLOW, LIVING_OCC)


def _register(hass) -> None:
    reg = er.async_get(hass)
    for entity_id in ALL:
        domain, object_id = entity_id.split(".", 1)
        state = hass.states.get(entity_id)
        hass.states.async_remove(entity_id)  # (else the registry picks another id)
        assert reg.async_get_or_create(domain, "demo", object_id, suggested_object_id=object_id).entity_id == entity_id
        if state is not None:
            hass.states.async_set(entity_id, state.state, state.attributes)


def _hidden(hass) -> dict[str, str]:
    reg = er.async_get(hass)
    entries = {e: reg.async_get(e) for e in ALL}
    return {e: r.hidden_by for e, r in entries.items() if r is not None and r.hidden_by is not None}


async def _level(hass, freezer, level: str) -> None:
    await set_setting(hass, "hide_used", level, entity_id=None)
    await tick(hass, freezer, 2)


async def _setup(hass, home, freezer):
    _register(hass)
    hass.states.async_set(LG, "off")
    hass.states.async_set(LIVING_OCC, "off")
    home.group("idle", [], FOLLOW)
    room = await setup_room(hass, display=[TV, LG], occupancy=[LIVING_OCC])
    hub = await setup_hub(hass)
    await tick(hass, freezer, 2)
    return room, hub


async def test_off_by_default(hass, home, freezer):
    await _setup(hass, home, freezer)
    assert _hidden(hass) == {}


async def test_the_levels(hass, home, freezer):
    await _setup(hass, home, freezer)
    await _level(hass, freezer, "players")
    assert _hidden(hass) == {KODI: INTEGRATION, MUSIC: INTEGRATION, FOLLOW: INTEGRATION}
    await _level(hass, freezer, "devices")
    assert _hidden(hass) == {KODI: INTEGRATION, MUSIC: INTEGRATION, FOLLOW: INTEGRATION, LG: INTEGRATION}
    await _level(hass, freezer, "all")
    assert set(_hidden(hass)) == {KODI, MUSIC, FOLLOW, LG, TV, SPEAKERS, BASS}, "not the presence sensor"
    await _level(hass, freezer, "players")
    assert set(_hidden(hass)) == {KODI, MUSIC, FOLLOW}
    await _level(hass, freezer, "none")
    assert _hidden(hass) == {}


async def test_what_was_hidden_by_hand_is_left_alone(hass, home, freezer):
    await _setup(hass, home, freezer)
    er.async_get(hass).async_update_entity(KODI, hidden_by=USER)
    await _level(hass, freezer, "players")
    await _level(hass, freezer, "none")
    assert _hidden(hass) == {KODI: USER}


async def test_one_shown_by_hand_stays_shown(hass, home, freezer):
    await _setup(hass, home, freezer)
    await _level(hass, freezer, "players")
    er.async_get(hass).async_update_entity(KODI, hidden_by=None)
    await hass.async_block_till_done()
    await _level(hass, freezer, "devices")
    assert KODI not in _hidden(hass)
    await _level(hass, freezer, "none")  # once to none: hidden again next time
    await _level(hass, freezer, "players")
    assert _hidden(hass).get(KODI) == INTEGRATION


async def test_shown_again_when_no_longer_used(hass, home, freezer):
    room, _hub = await _setup(hass, home, freezer)
    await _level(hass, freezer, "devices")
    await hass.config_entries.async_set_disabled_by(room.entry_id, ConfigEntryDisabler.USER)
    await hass.async_block_till_done()
    await tick(hass, freezer, 2)
    assert _hidden(hass) == {FOLLOW: INTEGRATION}, "a disabled room uses nothing"
    await hass.config_entries.async_set_disabled_by(room.entry_id, None)
    await hass.async_block_till_done()
    await tick(hass, freezer, 2)
    assert LG in _hidden(hass)
    await hass.config_entries.async_remove(room.entry_id)
    await hass.async_block_till_done()
    await tick(hass, freezer, 2)
    assert _hidden(hass) == {FOLLOW: INTEGRATION}


async def test_removing_the_central_entry_shows_everything(hass, home, freezer):
    _room, hub = await _setup(hass, home, freezer)
    await _level(hass, freezer, "all")
    await hass.config_entries.async_remove(hub.entry_id)
    await hass.async_block_till_done()
    await tick(hass, freezer, 2)
    assert _hidden(hass) == {}


async def test_a_reload_of_the_central_entry_keeps_them_hidden(hass, home, freezer):
    _room, hub = await _setup(hass, home, freezer)
    await _level(hass, freezer, "players")
    assert await hass.config_entries.async_reload(hub.entry_id)
    await hass.async_block_till_done()
    await tick(hass, freezer, 2)
    assert set(_hidden(hass)) == {KODI, MUSIC, FOLLOW}


async def test_an_entity_that_comes_late_is_hidden(hass, home, freezer):
    await _setup(hass, home, freezer)
    reg = er.async_get(hass)
    reg.async_remove(MUSIC)
    await hass.async_block_till_done()
    await _level(hass, freezer, "players")
    assert MUSIC not in _hidden(hass)
    hass.states.async_remove(MUSIC)
    reg.async_get_or_create("media_player", "demo", "office_player", suggested_object_id="office_player")
    await hass.async_block_till_done()
    await tick(hass, freezer, 2)
    assert _hidden(hass).get(MUSIC) == INTEGRATION


async def test_what_was_hidden_is_remembered_over_a_restart(hass, home, freezer, hass_storage):
    await _setup(hass, home, freezer)
    await _level(hass, freezer, "players")
    await tick(hass, freezer, 15)  # (saved)
    assert sorted(hass_storage["better_media_player.hidden"]["data"]["hidden"]) == sorted([KODI, MUSIC, FOLLOW])


async def test_shown_by_hand_and_no_longer_used_is_hidden_again_once_used(hass, home, freezer):
    room, _hub = await _setup(hass, home, freezer)
    await _level(hass, freezer, "players")
    er.async_get(hass).async_update_entity(KODI, hidden_by=None)
    await hass.async_block_till_done()
    await hass.config_entries.async_set_disabled_by(room.entry_id, ConfigEntryDisabler.USER)
    await hass.async_block_till_done()
    await tick(hass, freezer, 2)
    await hass.config_entries.async_set_disabled_by(room.entry_id, None)
    await hass.async_block_till_done()
    await tick(hass, freezer, 2)
    assert _hidden(hass).get(KODI) == INTEGRATION


async def test_only_its_own_hiding_is_undone(hass, home, freezer, hass_storage):
    """Remembered as hidden by this, but hidden by hand since: left as it is."""
    _register(hass)
    er.async_get(hass).async_update_entity(KODI, hidden_by=USER)
    hass_storage["better_media_player.hidden"] = {"version": 1, "key": "better_media_player.hidden", "data": {"hidden": [KODI], "shown": []}}
    await setup_room(hass)
    await setup_hub(hass)
    await tick(hass, freezer, 2)
    assert _hidden(hass) == {KODI: USER}


async def test_nothing_changes_while_the_central_entry_loads(hass, home, freezer, hass_storage, caplog):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.better_media_player.const import CONF_ENTRY_TYPE, DOMAIN, ENTRY_TYPE_HUB

    _register(hass)
    er.async_get(hass).async_update_entity(KODI, hidden_by=INTEGRATION)
    hass_storage["better_media_player.hidden"] = {"version": 1, "key": "better_media_player.hidden", "data": {"hidden": [KODI], "shown": []}}
    MockConfigEntry(domain=DOMAIN, data={CONF_ENTRY_TYPE: ENTRY_TYPE_HUB}, version=99).add_to_hass(hass)  # (does not load)
    await setup_room(hass)
    await tick(hass, freezer, 2)
    assert _hidden(hass) == {KODI: INTEGRATION}
    assert "hide.py" not in caplog.text


async def test_a_disabled_central_entry_shows_everything(hass, home, freezer):
    _room, hub = await _setup(hass, home, freezer)
    await _level(hass, freezer, "players")
    await hass.config_entries.async_set_disabled_by(hub.entry_id, ConfigEntryDisabler.USER)
    await hass.async_block_till_done()
    await tick(hass, freezer, 2)
    assert _hidden(hass) == {}


async def test_one_hidden_by_hand_and_shown_again_stays_shown(hass, home, freezer):
    await _setup(hass, home, freezer)
    reg = er.async_get(hass)
    reg.async_update_entity(KODI, hidden_by=USER)
    await _level(hass, freezer, "players")
    reg.async_update_entity(KODI, hidden_by=None)
    await hass.async_block_till_done()
    await _level(hass, freezer, "devices")  # (any later look)
    assert KODI not in _hidden(hass)


async def test_a_renamed_one_shown_by_hand_stays_shown(hass, home, freezer):
    room, _hub = await _setup(hass, home, freezer)
    reg = er.async_get(hass)
    await _level(hass, freezer, "players")
    reg.async_update_entity(KODI, hidden_by=None)
    await hass.async_block_till_done()
    reg.async_update_entity(KODI, new_entity_id="media_player.kodi_new")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)  # the room follows the new id (issues.py)
    await _level(hass, freezer, "devices")
    assert reg.async_get("media_player.kodi_new").hidden_by is None


async def test_one_that_comes_back_is_still_ours(hass, home, freezer):
    """The registry keeps hidden_by for a removed entity that is added again."""
    await _setup(hass, home, freezer)
    reg = er.async_get(hass)
    await _level(hass, freezer, "players")
    reg.async_remove(KODI)
    await hass.async_block_till_done()
    hass.states.async_remove(KODI)
    reg.async_get_or_create("media_player", "demo", "kodi", suggested_object_id="kodi")
    await hass.async_block_till_done()
    assert reg.async_get(KODI).hidden_by is INTEGRATION
    await _level(hass, freezer, "none")
    assert reg.async_get(KODI).hidden_by is None
