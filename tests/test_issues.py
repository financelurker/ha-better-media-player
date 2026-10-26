"""Repairs: renamed entities are followed; missing, disabled, shared entities and group players are issues."""

from __future__ import annotations

from datetime import timedelta

from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import selector as sel
from homeassistant.util import dt as dt_util

from custom_components.better_media_player.config_flow import _fields
from custom_components.better_media_player.const import (
    CONF_AUDIO_PLAYER,
    CONF_BASS,
    CONF_ENTRY_TYPE,
    CONF_FOLLOW_PLAYER,
    CONF_SPEAKERS,
    CONF_VOLUME_ENTITY,
    DEFAULTS,
    DOMAIN,
    ENTRY_TYPE_ROOM,
)
from custom_components.better_media_player.issues import START_DELAY, entity_keys

from .conftest import GROUP, KITCHEN, MUSIC, SPEAKERS, setup_hub, setup_room

GONE = "switch.gone_plug"
OCC = "binary_sensor.open_plan_occupancy"


async def _tick(hass, freezer, seconds: float) -> None:
    for _ in range(int(seconds)):
        freezer.tick(timedelta(seconds=1))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()


async def _after_start(hass, freezer) -> None:
    await _tick(hass, freezer, START_DELAY + 2)


def _issues(hass) -> dict[str, ir.IssueEntry]:
    return {i: e for (d, i), e in ir.async_get(hass).issues.items() if d == DOMAIN}


async def _kitchen(hass, **options) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Kitchen", data={CONF_ENTRY_TYPE: ENTRY_TYPE_ROOM},
        options={**DEFAULTS, CONF_AUDIO_PLAYER: KITCHEN, **options}, version=3,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def test_entity_keys_are_the_setups_entity_fields():
    keys = entity_keys()
    assert keys == [k for k, f in _fields().items() if isinstance(f, sel.EntitySelector)]
    assert {CONF_AUDIO_PLAYER, CONF_SPEAKERS, CONF_VOLUME_ENTITY, "occupancy", "display_power"} <= set(keys)


async def test_missing_entity_after_the_start_delay(hass, home, freezer):
    entry = await setup_room(hass, **{CONF_BASS: [GONE]})
    await _tick(hass, freezer, START_DELAY - 5)
    assert _issues(hass) == {}, "integrations may add their entities late"
    await _tick(hass, freezer, 7)
    issue = _issues(hass)[f"missing_entities_{entry.entry_id}"]
    assert issue.severity == ir.IssueSeverity.WARNING
    assert issue.translation_placeholders == {"room": "Wohnzimmer", "entities": f"- `{GONE}`"}
    # it comes (e.g. a YAML entity without unique id, later): the issue goes
    hass.states.async_set(GONE, "off")
    await _tick(hass, freezer, 3)
    assert _issues(hass) == {}


async def test_missing_player_is_an_error(hass, home, freezer):
    entry = await setup_room(hass, **{CONF_AUDIO_PLAYER: "media_player.gone"})
    await _after_start(hass, freezer)
    assert _issues(hass)[f"missing_entities_{entry.entry_id}"].severity == ir.IssueSeverity.ERROR


async def test_disabled_entity(hass, home, freezer):
    reg = er.async_get(hass)
    plug = reg.async_get_or_create(
        "switch", "test", "bass", suggested_object_id="bass_disabled", disabled_by=er.RegistryEntryDisabler.USER
    )
    entry = await setup_room(hass, **{CONF_BASS: [plug.entity_id]})
    await _after_start(hass, freezer)
    issues = _issues(hass)
    assert list(issues) == [f"disabled_entities_{entry.entry_id}"]
    assert plug.entity_id in issues[f"disabled_entities_{entry.entry_id}"].translation_placeholders["entities"]
    reg.async_update_entity(plug.entity_id, disabled_by=None)
    hass.states.async_set(plug.entity_id, "off")
    await _tick(hass, freezer, 3)
    assert _issues(hass) == {}


async def test_renamed_entity_is_followed(hass, home, freezer):
    reg = er.async_get(hass)
    plug = reg.async_get_or_create("switch", "test", "bass", suggested_object_id="old_bass")
    hass.states.async_set(plug.entity_id, "off")
    entry = await setup_room(hass, **{CONF_BASS: [plug.entity_id], CONF_SPEAKERS: [SPEAKERS]})
    await _after_start(hass, freezer)
    reg.async_update_entity(plug.entity_id, new_entity_id="switch.new_bass")
    hass.states.async_set("switch.new_bass", "off")
    await hass.async_block_till_done()
    assert entry.options[CONF_BASS] == ["switch.new_bass"]
    assert entry.options[CONF_SPEAKERS] == [SPEAKERS]
    await _tick(hass, freezer, 3)
    assert _issues(hass) == {}


async def test_renamed_follow_player_is_followed(hass, home, freezer):
    reg = er.async_get(hass)
    group = reg.async_get_or_create("media_player", "test", "group", suggested_object_id="old_group")
    home.group("idle", [], group.entity_id)
    hub = await setup_hub(hass, **{CONF_FOLLOW_PLAYER: group.entity_id})
    reg.async_update_entity(group.entity_id, new_entity_id="media_player.new_group")
    await hass.async_block_till_done()
    assert hub.options[CONF_FOLLOW_PLAYER] == "media_player.new_group"


async def test_shared_entities(hass, home, freezer):
    hass.states.async_set(OCC, "off")
    await setup_room(hass, occupancy=[OCC])
    kitchen = await _kitchen(hass, **{CONF_SPEAKERS: [SPEAKERS]})
    await _after_start(hass, freezer)
    issue = _issues(hass)["shared_entities"]
    assert issue.translation_placeholders == {"entities": f"- `{SPEAKERS}`: Kitchen, Wohnzimmer"}
    # sensors are only read: shared is fine
    hass.config_entries.async_update_entry(kitchen, options={**kitchen.options, CONF_SPEAKERS: [], "occupancy": [OCC]})
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert "shared_entities" not in _issues(hass)


async def test_group_player_as_room_player(hass, home, freezer):
    home.group("idle", [], GROUP)
    entry = await setup_room(hass, **{CONF_AUDIO_PLAYER: GROUP})
    await _after_start(hass, freezer)
    assert _issues(hass)[f"group_player_{entry.entry_id}"].translation_placeholders["entities"] == f"- `{GROUP}`"


async def test_follow_player_not_a_group(hass, home, freezer):
    hub = await setup_hub(hass, **{CONF_FOLLOW_PLAYER: MUSIC})
    await _after_start(hass, freezer)
    issue = _issues(hass)[f"follow_player_not_group_{hub.entry_id}"]
    assert issue.translation_placeholders["entity"] == MUSIC


async def test_volume_entity_left_from_before_is_not_checked(hass, home, freezer):
    await setup_room(hass, **{CONF_VOLUME_ENTITY: "number.gone"})  # the volume is on the players
    await _after_start(hass, freezer)
    assert _issues(hass) == {}


async def test_issues_go_with_the_room(hass, home, freezer):
    entry = await setup_room(hass, **{CONF_BASS: [GONE]})
    await _after_start(hass, freezer)
    assert _issues(hass)
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert _issues(hass) == {}
