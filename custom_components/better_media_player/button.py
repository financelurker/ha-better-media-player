"""'All off' (room) and 'Start follow' (central) buttons."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BetterMediaPlayerConfigEntry, is_hub
from .entity import BetterMediaPlayerEntity, HubEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterMediaPlayerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    if is_hub(entry):
        async_add_entities([StartFollowButton(entry, entry.runtime_data, "start_follow")])
        return
    async_add_entities([AllOffButton(entry, entry.runtime_data, "all_off")])


class AllOffButton(BetterMediaPlayerEntity, ButtonEntity):
    """Same as turning the room player off: stop sources, power down."""

    async def async_press(self) -> None:
        await self.ctrl.async_turn_off()


class StartFollowButton(HubEntity, ButtonEntity):
    """Continue the last follow queue in the rooms where someone is."""

    _attr_icon = "mdi:account-music"

    async def async_press(self) -> None:
        await self.hub.async_start_follow()
