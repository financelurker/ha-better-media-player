"""'All off' button."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BetterMediaPlayerConfigEntry
from .entity import BetterMediaPlayerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterMediaPlayerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([AllOffButton(entry, entry.runtime_data, "all_off")])


class AllOffButton(BetterMediaPlayerEntity, ButtonEntity):
    """Same as turning the room player off: stop sources, power down."""

    async def async_press(self) -> None:
        await self.ctrl.async_turn_off()
