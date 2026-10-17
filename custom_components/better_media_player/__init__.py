"""Better Media Player - one smart media player per room.

Combines the room's media players (e.g. Kodi, Music Assistant) with the power
plugs of TV, speakers and subwoofer into a single media_player entity.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, PLATFORMS, STORAGE_VERSION
from .controller import RoomController

type BetterMediaPlayerConfigEntry = ConfigEntry[RoomController]


async def async_setup_entry(hass: HomeAssistant, entry: BetterMediaPlayerConfigEntry) -> bool:
    controller = RoomController(hass, entry)
    await controller.async_start()
    entry.runtime_data = controller
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BetterMediaPlayerConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.async_stop()
    return ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete stored data of a removed room."""
    store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
    await store.async_remove()


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
