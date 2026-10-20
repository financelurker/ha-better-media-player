"""Config and options flow: what is connected (how a room behaves is set on its device)."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er

from custom_components.better_media_player.const import (
    CONF_AREA,
    CONF_AUDIO_HOST,
    CONF_AUDIO_PLAYER,
    CONF_DISPLAY,
    CONF_ENTRY_TYPE,
    CONF_SPEAKERS,
    CONF_VIDEO_PLAYER,
    DOMAIN,
    ENTRY_TYPE_ROOM,
)

from .conftest import GROUP, KODI, LIVING_OCC, MUSIC, SPEAKERS, TV, setup_room

PLAYERS = {"special": {}}
DEVICES = {"standby": {}, "follow": {}}


async def _start(hass: HomeAssistant, area: str | None = None):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "room"})
    assert result["type"] is FlowResultType.FORM
    data = {"name": "Wohnzimmer"}
    if area:
        data[CONF_AREA] = area
    return await hass.config_entries.flow.async_configure(result["flow_id"], data)


async def test_full_flow(hass, home):
    result = await _start(hass)
    assert result["step_id"] == "players"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_VIDEO_PLAYER: KODI, CONF_AUDIO_PLAYER: MUSIC, **PLAYERS}
    )
    assert result["step_id"] == "devices"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_DISPLAY: [TV], CONF_SPEAKERS: [SPEAKERS], "standby": {}, "follow": {"occupancy": [LIVING_OCC]}},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Wohnzimmer"
    assert result["data"] == {CONF_ENTRY_TYPE: ENTRY_TYPE_ROOM}
    assert result["options"] == {
        CONF_VIDEO_PLAYER: KODI,
        CONF_AUDIO_PLAYER: MUSIC,
        CONF_DISPLAY: [TV],
        CONF_SPEAKERS: [SPEAKERS],
        "occupancy": [LIVING_OCC],
        "volume_control": "players",
    }, "only what is connected: no times, waits or other behaviour"
    await hass.async_block_till_done()
    assert hass.states.get("media_player.wohnzimmer") is not None
    assert float(hass.states.get("number.wohnzimmer_speaker_run_down").state) == 2  # the device's default


async def test_validation(hass, home):
    home.group("idle", [])
    result = await _start(hass)
    flow = result["flow_id"]

    result = await hass.config_entries.flow.async_configure(flow, PLAYERS)
    assert result["errors"] == {CONF_VIDEO_PLAYER: "no_player"}

    result = await hass.config_entries.flow.async_configure(flow, {CONF_AUDIO_PLAYER: GROUP, **PLAYERS})
    assert result["errors"] == {CONF_AUDIO_PLAYER: "group_player"}

    result = await hass.config_entries.flow.async_configure(flow, {CONF_AUDIO_PLAYER: MUSIC, **PLAYERS})
    assert result["step_id"] == "devices"
    result = await hass.config_entries.flow.async_configure(flow, {CONF_DISPLAY: [TV], **DEVICES})
    assert result["errors"] == {CONF_DISPLAY: "display_needs_video"}
    result = await hass.config_entries.flow.async_configure(
        flow, {CONF_SPEAKERS: [SPEAKERS], "bass": [SPEAKERS], **DEVICES}
    )
    assert result["errors"] == {"bass": "duplicate_device"}
    result = await hass.config_entries.flow.async_configure(flow, {CONF_SPEAKERS: [SPEAKERS], **DEVICES})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_an_error_in_a_section_is_shown_at_the_section(hass, home):
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_VIDEO_PLAYER: KODI, "special": {CONF_AUDIO_HOST: "switch.kodi_box_plug"}}
    )
    assert result["errors"] == {"special": "needs_player"}


async def test_area_preselects_players(hass, home):
    area = ar.async_get(hass).async_create("Wohnzimmer")
    reg = er.async_get(hass)
    kodi = reg.async_get_or_create("media_player", "kodi", "kodi1", suggested_object_id="kodi")
    music = reg.async_get_or_create("media_player", "music_assistant", "ma1", suggested_object_id="office_player")
    reg.async_update_entity(kodi.entity_id, area_id=area.id)
    reg.async_update_entity(music.entity_id, area_id=area.id)

    result = await _start(hass, area.id)
    assert result["step_id"] == "players"
    schema = result["data_schema"].schema
    suggested = {str(k): k.description.get("suggested_value") for k in schema if k.description}
    assert suggested[CONF_VIDEO_PLAYER] == kodi.entity_id
    assert suggested[CONF_AUDIO_PLAYER] == music.entity_id


async def test_options_flow(hass, home):
    entry = await setup_room(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["room", "players", "devices", "save"]
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "devices"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_DISPLAY: [TV], CONF_SPEAKERS: [SPEAKERS], "standby": {}, "follow": {"occupancy": [LIVING_OCC]}}
    )
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "save"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["occupancy"] == [LIVING_OCC]
    assert "bass" not in entry.options  # cleared
    assert hass.states.get("number.wohnzimmer_follow_me_leave_after") is not None


async def test_options_show_the_sections_values(hass, home):
    entry = await setup_room(hass, occupancy=[LIVING_OCC])
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "devices"})
    sections = {str(k): v for k, v in result["data_schema"].schema.items()}
    follow = {str(k): k.description.get("suggested_value") for k in sections["follow"].schema.schema if k.description}
    assert follow == {"occupancy": [LIVING_OCC]}
