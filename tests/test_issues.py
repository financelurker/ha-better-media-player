"""Repairs: renamed entities are followed; missing, disabled, shared entities and group players are issues."""

from __future__ import annotations

from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import CoreState
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import selector as sel

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
from custom_components.better_media_player.issues import START_DELAY, entity_keys, get_checker

from .conftest import GROUP, KITCHEN, MUSIC, SPEAKERS, setup_hub, setup_room
from .helpers import tick

GONE = "switch.gone_plug"
OCC = "binary_sensor.open_plan_occupancy"


async def _after_start(hass, freezer) -> None:
    await tick(hass, freezer, START_DELAY + 2)


def _issues(hass) -> dict[str, ir.IssueEntry]:
    return {i: e for (d, i), e in ir.async_get(hass).issues.items() if d == DOMAIN}


def _issue(hass, prefix: str) -> ir.IssueEntry:
    found = [e for i, e in _issues(hass).items() if i.startswith(prefix)]
    assert len(found) == 1, (prefix, list(_issues(hass)))
    return found[0]


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
    await tick(hass, freezer, START_DELAY - 5)
    assert _issues(hass) == {}, "integrations may add their entities late"
    await tick(hass, freezer, 7)
    issue = _issue(hass, f"missing_entities_{entry.entry_id}")
    assert issue.severity == ir.IssueSeverity.WARNING
    assert issue.translation_placeholders == {"room": "Living Room", "entities": f"- `{GONE}`"}
    # it comes (e.g. a YAML entity without unique id, later): the issue goes
    hass.states.async_set(GONE, "off")
    await tick(hass, freezer, 3)
    assert _issues(hass) == {}


async def test_missing_player_is_an_error(hass, home, freezer):
    entry = await setup_room(hass, **{CONF_AUDIO_PLAYER: "media_player.gone"})
    await _after_start(hass, freezer)
    assert _issue(hass, f"missing_entities_{entry.entry_id}").severity == ir.IssueSeverity.ERROR


async def test_disabled_entity(hass, home, freezer):
    reg = er.async_get(hass)
    plug = reg.async_get_or_create(
        "switch", "test", "bass", suggested_object_id="bass_disabled", disabled_by=er.RegistryEntryDisabler.USER
    )
    entry = await setup_room(hass, **{CONF_BASS: [plug.entity_id]})
    await _after_start(hass, freezer)
    assert len(_issues(hass)) == 1
    assert plug.entity_id in _issue(hass, f"disabled_entities_{entry.entry_id}").translation_placeholders["entities"]
    reg.async_update_entity(plug.entity_id, disabled_by=None)
    hass.states.async_set(plug.entity_id, "off")
    await tick(hass, freezer, 3)
    assert _issues(hass) == {}


async def test_renamed_entity_is_followed(hass, home, freezer):
    reg = er.async_get(hass)
    plug = reg.async_get_or_create("switch", "test", "bass", suggested_object_id="old_bass")
    hass.states.async_set(plug.entity_id, "off")
    entry = await setup_room(hass, **{CONF_BASS: [plug.entity_id], CONF_SPEAKERS: [SPEAKERS]})
    await _after_start(hass, freezer)
    reg.async_update_entity(plug.entity_id, new_entity_id="switch.new_bass")
    hass.states.async_set("switch.new_bass", "off")
    await tick(hass, freezer, 1)
    assert entry.options[CONF_BASS] == ["switch.new_bass"]
    assert entry.options[CONF_SPEAKERS] == [SPEAKERS]
    await tick(hass, freezer, 3)
    assert _issues(hass) == {}


async def test_renamed_follow_player_is_followed(hass, home, freezer):
    reg = er.async_get(hass)
    group = reg.async_get_or_create("media_player", "test", "group", suggested_object_id="old_group")
    home.group("idle", [], group.entity_id)
    hub = await setup_hub(hass, **{CONF_FOLLOW_PLAYER: group.entity_id})
    reg.async_update_entity(group.entity_id, new_entity_id="media_player.new_group")
    await tick(hass, freezer, 1)
    assert hub.options[CONF_FOLLOW_PLAYER] == "media_player.new_group"


async def test_shared_entities(hass, home, freezer):
    hass.states.async_set(OCC, "off")
    await setup_room(hass, occupancy=[OCC])
    kitchen = await _kitchen(hass, **{CONF_SPEAKERS: [SPEAKERS]})
    await _after_start(hass, freezer)
    issue = _issue(hass, "shared_entities")
    assert issue.translation_placeholders == {"entities": f"- `{SPEAKERS}`: Kitchen, Living Room"}
    # sensors are only read: shared is fine
    hass.config_entries.async_update_entry(kitchen, options={**kitchen.options, CONF_SPEAKERS: [], "occupancy": [OCC]})
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert not [i for i in _issues(hass) if i.startswith("shared_entities")]


async def test_group_player_as_room_player(hass, home, freezer):
    home.group("idle", [], GROUP)
    entry = await setup_room(hass, **{CONF_AUDIO_PLAYER: GROUP})
    await _after_start(hass, freezer)
    assert _issue(hass, f"group_player_{entry.entry_id}").translation_placeholders["entities"] == f"- `{GROUP}`"


async def test_follow_player_not_a_group(hass, home, freezer):
    hub = await setup_hub(hass, **{CONF_FOLLOW_PLAYER: MUSIC})
    await _after_start(hass, freezer)
    issue = _issue(hass, f"follow_player_not_group_{hub.entry_id}")
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
    await tick(hass, freezer, 3)
    assert _issues(hass) == {}


async def test_missing_follow_player(hass, home, freezer):
    hub = await setup_hub(hass, **{CONF_FOLLOW_PLAYER: "media_player.gone_group"})
    await _after_start(hass, freezer)
    issue = _issue(hass, f"follow_player_missing_{hub.entry_id}")
    assert issue.severity == ir.IssueSeverity.ERROR
    assert issue.translation_placeholders["entity"] == "media_player.gone_group"


async def test_group_check_waits_for_a_state_that_tells(hass, home, freezer):
    """Music Assistant may still be loading: unavailable says nothing about a group."""
    hass.states.async_set(GROUP, "unavailable")
    await setup_hub(hass, **{CONF_FOLLOW_PLAYER: GROUP})
    hass.states.async_set(KITCHEN, "unavailable")
    room = await _kitchen(hass)
    await _after_start(hass, freezer)
    assert _issues(hass) == {}
    home.group("idle", [], GROUP)  # it comes up, as a group: fine
    hass.states.async_set(KITCHEN, "idle", {"mass_player_type": "group"})  # comes up as a group: not fine
    await tick(hass, freezer, 3)
    assert len(_issues(hass)) == 1
    _issue(hass, f"group_player_{room.entry_id}")  # (and nothing for the follow-me player)


async def test_follow_player_that_comes_up_as_no_group(hass, home, freezer):
    hass.states.async_set(MUSIC, "unavailable")
    hub = await setup_hub(hass, **{CONF_FOLLOW_PLAYER: MUSIC})
    await _after_start(hass, freezer)
    assert _issues(hass) == {}
    home.music("idle")
    await tick(hass, freezer, 3)
    _issue(hass, f"follow_player_not_group_{hub.entry_id}")


async def test_several_renames_reload_once(hass, home, freezer):
    reg = er.async_get(hass)
    bass = reg.async_get_or_create("switch", "test", "bass", suggested_object_id="dev_bass")
    spk = reg.async_get_or_create("switch", "test", "spk", suggested_object_id="dev_speakers")
    entry = await setup_room(hass, **{CONF_BASS: [bass.entity_id], CONF_SPEAKERS: [spk.entity_id]})
    await _after_start(hass, freezer)
    with patch.object(hass.config_entries, "async_update_entry", wraps=hass.config_entries.async_update_entry) as update:
        reg.async_update_entity(bass.entity_id, new_entity_id="switch.room_bass")
        reg.async_update_entity(spk.entity_id, new_entity_id="switch.room_speakers")
        await tick(hass, freezer, 1)
    assert [c.args[0].entry_id for c in update.call_args_list] == [entry.entry_id]
    assert (entry.options[CONF_BASS], entry.options[CONF_SPEAKERS]) == (["switch.room_bass"], ["switch.room_speakers"])


async def test_a_dismissed_issue_does_not_hide_new_ones(hass, home, freezer):
    entry = await setup_room(hass, **{CONF_BASS: [GONE]})
    await _after_start(hass, freezer)
    first = next(iter(_issues(hass)))
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_SPEAKERS: ["switch.gone_too"]})
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert list(_issues(hass)) != [first]
    assert len(_issues(hass)) == 1


async def test_other_integrations_issues_stay(hass, home, freezer):
    ir.async_create_issue(hass, "other", "theirs", is_fixable=False, severity=ir.IssueSeverity.WARNING, translation_key="x")
    await setup_room(hass)
    await _after_start(hass, freezer)
    assert ("other", "theirs") in ir.async_get(hass).issues


async def test_start_with_home_assistant_and_stop(hass, home, freezer, caplog):
    """Set up while Home Assistant starts: checked a minute after it has started; a clean stop."""
    hass.set_state(CoreState.not_running)
    await setup_room(hass, **{CONF_BASS: [GONE]})
    await tick(hass, freezer, START_DELAY + 5)
    assert _issues(hass) == {}, "not started yet"
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    await tick(hass, freezer, START_DELAY + 2)
    assert len(_issues(hass)) == 1
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert "Unable to remove unknown" not in caplog.text
    checker = get_checker(hass)
    checker.changed()  # an entry change during the stop: no new timer
    assert checker._later is None
