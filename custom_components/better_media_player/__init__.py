"""Better Media Player - one smart media player per room.

Combines the room's media players (e.g. Kodi, Music Assistant) with the power
plugs of TV, speakers and subwoofer into a single media_player entity.
A central entry adds "follow me" with a Music Assistant group player.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .const import (
    CONF_ENTRY_TYPE,
    CONF_FOLLOW_PLAYER,
    DOMAIN,
    ENTRY_TYPE_HUB,
    HUB_PLATFORMS,
    PLATFORMS,
    STORAGE_VERSION,
)
from .controller import RoomController
from .hub import FollowManager, get_rooms

type BetterMediaPlayerConfigEntry = ConfigEntry[RoomController | FollowManager]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


def is_hub(entry: ConfigEntry) -> bool:
    return entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_HUB


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    hass.data.setdefault(DOMAIN, {}).setdefault("rooms", {})
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """1 -> 2: the central entry has one follow-me player instead of group policies."""
    if entry.version > 2:
        return False
    if entry.version == 1:
        options = dict(entry.options)
        if is_hub(entry):
            follow = list(options.pop("follow_groups", None) or [])
            options.pop("always_groups", None)
            if follow:
                options[CONF_FOLLOW_PLAYER] = follow[0]
            # the central "start follow" button is now a button in each room
            reg = er.async_get(hass)
            if old := reg.async_get_entity_id("button", DOMAIN, f"{entry.entry_id}_start_follow"):
                reg.async_remove(old)
        hass.config_entries.async_update_entry(entry, options=options, version=2)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: BetterMediaPlayerConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {}).setdefault("rooms", {})
    if "version" not in hass.data[DOMAIN]:
        hass.data[DOMAIN]["version"] = str((await async_get_integration(hass, DOMAIN)).version)
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
            for room in get_rooms(hass).values():
                room.set_prewarm(False)
                room.notify()  # no follow me without the central entry
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
