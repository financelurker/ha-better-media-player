"""Config and options flow."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er

from custom_components.better_media_player.const import (
    CONF_AREA,
    CONF_AUDIO_PLAYER,
    CONF_DISPLAY,
    CONF_DISPLAY_STANDBY,
    CONF_ENTRY_TYPE,
    CONF_SPEAKERS,
    CONF_VIDEO_PLAYER,
    DOMAIN,
    ENTRY_TYPE_ROOM,
)

from .conftest import GROUP, KODI, MUSIC, SPEAKERS, TV, setup_room


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
    assert result["step_id"] == "sources"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_VIDEO_PLAYER: KODI, CONF_AUDIO_PLAYER: MUSIC}
    )
    assert result["step_id"] == "volume"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "devices"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DISPLAY: [TV], CONF_SPEAKERS: [SPEAKERS]}
    )
    assert result["step_id"] == "advanced"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "timing"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "standby"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "night"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "follow"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Wohnzimmer"
    assert result["data"] == {CONF_ENTRY_TYPE: ENTRY_TYPE_ROOM}
    assert result["options"][CONF_VIDEO_PLAYER] == KODI
    assert result["options"]["audio_run_down"] == 2
    assert result["options"]["night_max_volume"] == 30
    await hass.async_block_till_done()
    assert hass.states.get("media_player.wohnzimmer") is not None


async def test_validation(hass, home):
    home.group("idle", [])
    result = await _start(hass)
    flow = result["flow_id"]

    result = await hass.config_entries.flow.async_configure(flow, {})
    assert result["errors"] == {CONF_VIDEO_PLAYER: "no_player"}

    result = await hass.config_entries.flow.async_configure(flow, {CONF_AUDIO_PLAYER: GROUP})
    assert result["errors"] == {CONF_AUDIO_PLAYER: "group_player"}

    result = await hass.config_entries.flow.async_configure(flow, {CONF_AUDIO_PLAYER: MUSIC})
    assert result["step_id"] == "volume"
    result = await hass.config_entries.flow.async_configure(flow, {"power_on_preset": "party", "preset_party": 0})
    assert result["errors"] == {"power_on_preset": "preset_not_offered"}
    result = await hass.config_entries.flow.async_configure(flow, {})
    assert result["step_id"] == "devices"
    result = await hass.config_entries.flow.async_configure(flow, {CONF_DISPLAY: [TV]})
    assert result["errors"] == {CONF_DISPLAY: "display_needs_video"}
    result = await hass.config_entries.flow.async_configure(
        flow, {CONF_SPEAKERS: [SPEAKERS], "bass": [SPEAKERS]}
    )
    assert result["errors"] == {"bass": "duplicate_device"}
    result = await hass.config_entries.flow.async_configure(flow, {CONF_SPEAKERS: [SPEAKERS]})
    result = await hass.config_entries.flow.async_configure(flow, {})
    result = await hass.config_entries.flow.async_configure(flow, {})
    result = await hass.config_entries.flow.async_configure(flow, {CONF_DISPLAY_STANDBY: True})
    assert result["errors"] == {CONF_DISPLAY_STANDBY: "needs_power"}


async def test_area_preselects_players(hass, home):
    area = ar.async_get(hass).async_create("Wohnzimmer")
    reg = er.async_get(hass)
    kodi = reg.async_get_or_create("media_player", "kodi", "kodi1", suggested_object_id="kodi")
    music = reg.async_get_or_create("media_player", "music_assistant", "ma1", suggested_object_id="office_player")
    reg.async_update_entity(kodi.entity_id, area_id=area.id)
    reg.async_update_entity(music.entity_id, area_id=area.id)

    result = await _start(hass, area.id)
    assert result["step_id"] == "sources"
    schema = result["data_schema"].schema
    suggested = {str(k): k.description.get("suggested_value") for k in schema if k.description}
    assert suggested[CONF_VIDEO_PLAYER] == kodi.entity_id
    assert suggested[CONF_AUDIO_PLAYER] == music.entity_id


async def test_options_flow(hass, home):
    entry = await setup_room(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "timing"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"audio_run_down": 5, "display_run_down": 15}
    )
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "save"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["audio_run_down"] == 5
    ctrl = hass.config_entries.async_get_entry(entry.entry_id).runtime_data
    assert ctrl.data["audio_run_down"] == 5
