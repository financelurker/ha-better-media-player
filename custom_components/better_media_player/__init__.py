"""Better Media Player - one smart media player per room.

Combines the room's media players (e.g. Kodi, Music Assistant) with the power
plugs of TV, speakers and subwoofer into a single media_player entity.
A central entry adds group policies and "follow me" for Music Assistant groups.
"""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_FALLBACK_ROOMS,
    ATTR_GROUP,
    ATTR_VOLUME,
    CONF_ENTRY_TYPE,
    DOMAIN,
    ENTRY_TYPE_HUB,
    HUB_PLATFORMS,
    PLATFORMS,
    SERVICE_START_FOLLOW,
    STORAGE_VERSION,
)
from .controller import RoomController
from .hub import FollowManager, get_hub, get_rooms

type BetterMediaPlayerConfigEntry = ConfigEntry[RoomController | FollowManager]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

START_FOLLOW_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_GROUP): cv.entity_id,
        vol.Optional(ATTR_VOLUME): vol.All(vol.Coerce(float), vol.Range(min=0, max=1)),
        vol.Optional("media_content_id"): cv.string,
        vol.Optional("media_content_type"): cv.string,
        vol.Optional(ATTR_FALLBACK_ROOMS): cv.entity_ids,
    }
)


def is_hub(entry: ConfigEntry) -> bool:
    return entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_HUB


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    hass.data.setdefault(DOMAIN, {}).setdefault("rooms", {})

    async def _start_follow(call: ServiceCall) -> None:
        hub = get_hub(hass)
        if hub is None:
            raise HomeAssistantError("Better Media Player: central settings are not set up")
        rooms = [_room_for(hass, e) for e in call.data.get(ATTR_FALLBACK_ROOMS, [])]
        await hub.async_start_follow(
            group=call.data.get(ATTR_GROUP),
            volume=call.data.get(ATTR_VOLUME),
            media_content_id=call.data.get("media_content_id"),
            media_content_type=call.data.get("media_content_type"),
            fallback_rooms=[r for r in rooms if r is not None],
        )

    hass.services.async_register(DOMAIN, SERVICE_START_FOLLOW, _start_follow, schema=START_FOLLOW_SCHEMA)
    return True


def _room_for(hass: HomeAssistant, entity_id: str) -> RoomController | None:
    """A room by its room player entity, or by its music player."""
    rooms = get_rooms(hass)
    entry = er.async_get(hass).async_get(entity_id)
    if entry is not None and entry.config_entry_id in rooms:
        return rooms[entry.config_entry_id]
    for room in rooms.values():
        if room.audio_player == entity_id:
            return room
    return None


async def async_setup_entry(hass: HomeAssistant, entry: BetterMediaPlayerConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {}).setdefault("rooms", {})
    if is_hub(entry):
        hub = FollowManager(hass, entry)
        hass.data[DOMAIN]["hub"] = hub
        await hub.async_start()
        entry.runtime_data = hub
        await hass.config_entries.async_forward_entry_setups(entry, HUB_PLATFORMS)
    else:
        controller = RoomController(hass, entry)
        await controller.async_start()
        entry.runtime_data = controller
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BetterMediaPlayerConfigEntry) -> bool:
    platforms = HUB_PLATFORMS if is_hub(entry) else PLATFORMS
    ok = await hass.config_entries.async_unload_platforms(entry, platforms)
    if ok:
        await entry.runtime_data.async_stop()
        if is_hub(entry):
            hass.data[DOMAIN].pop("hub", None)
            # rooms fall back to v0.1 behaviour (every group counts)
            for room in get_rooms(hass).values():
                room.set_prewarm(False)
    return ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete stored data of a removed entry."""
    store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
    await store.async_remove()
    if is_hub(entry):
        # a re-added central entry starts fresh instead of inheriting an old session
        hass.data.get(DOMAIN, {}).pop("session", None)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
