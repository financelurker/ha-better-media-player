"""Better Media Player - one smart media player per room.

Combines the room's media players (e.g. Kodi, Music Assistant) with the power
plugs of TV, speakers and subwoofer into a single media_player entity.
A central entry adds "follow me" with a Music Assistant group player.
"""

from __future__ import annotations

from homeassistant.config_entries import SIGNAL_CONFIG_ENTRY_CHANGED, ConfigEntry, ConfigEntryChange
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .const import (
    CONF_ENTRY_TYPE,
    CONF_FOLLOW_PLAYER,
    DOMAIN,
    ENTITY_DEFAULTS,
    ENTRY_TYPE_HUB,
    HUB_ENTITY_DEFAULTS,
    HUB_PLATFORMS,
    PLATFORMS,
    STORAGE_VERSION,
)
from .room.announce import async_setup_services
from .room.controller import RoomController
from .hub import FollowManager, get_rooms
from .issues import async_setup_issues, get_checker

type BetterMediaPlayerConfigEntry = ConfigEntry[RoomController | FollowManager]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


def is_hub(entry: ConfigEntry) -> bool:
    return entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_HUB


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    hass.data.setdefault(DOMAIN, {}).setdefault("rooms", {})
    async_setup_services(hass)
    async_setup_issues(hass)

    @callback
    def _entry_changed(change: ConfigEntryChange, entry: ConfigEntry) -> None:
        if entry.domain == DOMAIN and (checker := get_checker(hass)) is not None:
            checker.changed()  # added, removed, set up, unloaded, options changed

    async_dispatcher_connect(hass, SIGNAL_CONFIG_ENTRY_CHANGED, _entry_changed)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """1 -> 2: the central entry has one follow-me player instead of group policies.
    2 -> 3: values set on the room device (number entities) are no longer options."""
    if entry.version > 3:
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
    if entry.version == 2:
        # how a room (or follow me) behaves is set on its device now: the
        # values move from the options to the device's stored values
        options = dict(entry.options)
        defaults = HUB_ENTITY_DEFAULTS if is_hub(entry) else ENTITY_DEFAULTS
        store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        data = await store.async_load() or {}
        for key, default in defaults.items():
            marker = data.pop(f"cfg_{key}", None)
            if key not in options:
                continue
            raw = options.pop(key)
            if isinstance(default, bool) or not isinstance(default, (int, float)):
                value = type(default)(raw)
            else:
                value = float(raw or 0)
            # an option change not yet taken by the entity wins (as before)
            if key not in data or marker != value:
                data[key] = value
        await store.async_save(data)
        hass.config_entries.async_update_entry(entry, options=options, version=3)
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

        @callback
        def _central_changed(change: ConfigEntryChange, changed: ConfigEntry) -> None:
            if is_hub(changed):  # loaded, disabled, failed, removed: the dynamic controls' settings
                controller.notify()

        entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_CONFIG_ENTRY_CHANGED, _central_changed))
        @callback
        def _started(_event: Event) -> None:
            controller.notify()  # a central entry that is never set up (disabled): its defaults count from now

        if not hass.is_running:
            entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _started))
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
                room.follow.set_prewarm(False)
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
