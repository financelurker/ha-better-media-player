"""Setup: the central entry. Rooms are set up and changed in the panel.

"Add integration" sets up the central entry (follow me's player, optional) or,
when there is one, points to the panel. A room's entry is created by the panel
(source "panel", without a form); its "Configure" leads to the panel
(config_panel_domain, panel/__init__.py).
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers import selector as sel

from .const import CONF_ENTRY_TYPE, CONF_FOLLOW_PLAYER, DOMAIN, ENTRY_TYPE_HUB, ENTRY_TYPE_ROOM, PANEL_URL
from .players.music_assistant import MusicAssistant


class BetterMediaPlayerConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 4
    MINOR_VERSION = 3

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        if any(e.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_HUB for e in self._async_current_entries()):
            return self.async_abort(reason="use_panel", description_placeholders={"panel": f"/{PANEL_URL}"})
        await self.async_set_unique_id(ENTRY_TYPE_HUB)
        self._abort_if_unique_id_configured()
        errors: dict[str, str] = {}
        if user_input is not None:
            player = user_input.get(CONF_FOLLOW_PLAYER)
            # follow me moves queues: a group player of Music Assistant
            if player and self.hass.states.get(player) is not None and not MusicAssistant(self.hass).is_group(player):
                errors[CONF_FOLLOW_PLAYER] = "not_a_group"
            else:
                return self.async_create_entry(
                    title="Better Media Player",
                    data={CONF_ENTRY_TYPE: ENTRY_TYPE_HUB},
                    options={CONF_FOLLOW_PLAYER: player} if player else {},
                    description_placeholders={"panel": f"/{PANEL_URL}"},
                )
        schema = vol.Schema(
            {vol.Optional(CONF_FOLLOW_PLAYER): sel.EntitySelector(sel.EntitySelectorConfig(domain="media_player"))}
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or {}),
            errors=errors,
            description_placeholders={"panel": f"/{PANEL_URL}"},
        )

    async def async_step_panel(self, data: dict[str, Any]) -> ConfigFlowResult:
        """A room from the panel (checked there: panel/edit.py)."""
        return self.async_create_entry(title=data["title"], data={CONF_ENTRY_TYPE: ENTRY_TYPE_ROOM}, options=data["options"])
