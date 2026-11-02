"""Setup: "Add integration" sets up the central entry; rooms come from the panel."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.better_media_player.const import CONF_ENTRY_TYPE, DOMAIN, ENTRY_TYPE_HUB, ENTRY_TYPE_ROOM

from .conftest import FOLLOW, setup_room


async def test_add_integration_sets_up_the_central_entry(hass, home):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    assert result["description_placeholders"] == {"panel": "/better-media-player"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ENTRY_TYPE: ENTRY_TYPE_HUB} and result["options"] == {}


async def test_with_a_central_entry_it_points_to_the_panel(hass, home):
    home.group("idle", [], FOLLOW)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    await hass.config_entries.flow.async_configure(result["flow_id"], {"follow_player": FOLLOW})
    await hass.async_block_till_done()
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "use_panel"


async def test_rooms_alone_still_let_the_central_entry_be_added(hass, home):
    """A room set up before (or only rooms): "Add integration" adds the central entry."""
    await setup_room(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM


async def test_a_room_from_the_panel(hass, home):
    options = {"sources": [{"id": "audio", "player": "media_player.office_player"}], "devices": [], "cables": [], "volume": {"where": "players"}}
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "panel"}, data={"title": "Office", "options": options})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert (entry.title, entry.data, dict(entry.options), entry.version) == ("Office", {CONF_ENTRY_TYPE: ENTRY_TYPE_ROOM}, options, 4)


async def test_no_options_flow_any_more(hass, home):
    """Configure leads to the panel (config_panel_domain), not to a dialog."""
    entry = await setup_room(hass)
    assert not entry.supports_options
