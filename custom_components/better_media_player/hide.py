"""Hide the entities the rooms use (central setting hide_used).

The rooms and follow me stand in for their players and devices: with the
setting on, these entities are hidden in Home Assistant (hidden_by:
integration), by level: the players, the players and the devices' media
players, or everything a room is set up with (not its presence sensors).

- Only what this hid is shown again: when the level goes down, when no
  enabled room (or follow me) uses it any more, and when the central entry is
  removed or disabled (then the setting is none). What someone hid or showed themselves is left alone; one shown
  again by hand stays shown until the setting is set to none once.
- An entity without a registry entry (no unique id) cannot be hidden.

Kept in a store of its own (it outlives the central entry's reloads).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import CALLBACK_TYPE, CoreState, Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store

from .const import (
    CONF_ENTRY_TYPE,
    CONF_FOLLOW_PLAYER,
    CONF_HIDE_USED,
    DOMAIN,
    ENTRY_TYPE_HUB,
    HIDE_NONE,
    STORAGE_VERSION,
)
from .model import used_by_level

_LOGGER = logging.getLogger(__name__)

STORE_KEY = f"{DOMAIN}.hidden"
DELAY = 1  # s: changes come in bursts (a reload, several registry updates)


def _is_hub(entry: ConfigEntry) -> bool:
    return entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_HUB


class Hider:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store = Store(hass, STORAGE_VERSION, STORE_KEY)
        self.hidden: set[str] = set()  # hidden by this
        self.shown: set[str] = set()  # hidden by this, shown again by hand: left shown
        self._unsubs: list[CALLBACK_TYPE] = []
        self._later: CALLBACK_TYPE | None = None
        self._started: CALLBACK_TYPE | None = None
        self._ready = False
        self._stopped = False

    async def async_start(self) -> None:
        data = await self._store.async_load() or {}
        self.hidden = set(data.get("hidden") or [])
        self.shown = set(data.get("shown") or [])
        self._unsubs.append(self.hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, self._registry_updated))
        if self.hass.state is CoreState.running:
            self._ready = True
            self.changed()
            return

        @callback
        def _started(_event: Event) -> None:
            self._started = None  # (a fired once-listener is gone: no unsub any more)
            self._ready = True
            self.changed()

        self._started = self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _started)

    @callback
    def stop(self) -> None:
        self._stopped = True
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._started:
            self._started()
            self._started = None
        if self._later:
            self._later()
            self._later = None

    @callback
    def changed(self) -> None:
        """Rooms, the central entry or its setting changed: look again (soon)."""
        if not self._ready or self._stopped or self._later is not None:
            return

        @callback
        def _run(_now) -> None:
            self._later = None
            self.update()

        self._later = async_call_later(self.hass, DELAY, _run)

    # --- what is wanted ---------------------------------------------------------

    def _level(self) -> str | None:
        """The setting; None: not known right now (the central entry is loading)."""
        hub = next((e for e in self.hass.config_entries.async_entries(DOMAIN) if _is_hub(e)), None)
        if hub is None or hub.disabled_by is not None:
            return HIDE_NONE
        if hub.state is not ConfigEntryState.LOADED:
            return None
        return hub.runtime_data.setting(CONF_HIDE_USED)

    def wanted(self, level: str) -> set[str]:
        out: set[str] = set()
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if entry.disabled_by is not None:
                continue
            if _is_hub(entry):
                if level != HIDE_NONE and (player := entry.options.get(CONF_FOLLOW_PLAYER)):
                    out.add(player)
                continue
            out.update(used_by_level(entry.options, level))
        return out

    # --- doing it ---------------------------------------------------------------

    @callback
    def update(self) -> None:
        level = self._level()
        if level is None:
            return
        self._apply(self.wanted(level))  # (none: nothing wanted, every mark goes)

    @callback
    def _apply(self, wanted: set[str]) -> None:
        reg = er.async_get(self.hass)
        before = (set(self.hidden), set(self.shown))
        for entity_id in sorted(self.hidden - wanted):
            self.hidden.discard(entity_id)  # (first: the registry event is not taken for a hand's)
            entry = reg.async_get(entity_id)
            if entry is not None and entry.hidden_by is er.RegistryEntryHider.INTEGRATION:
                reg.async_update_entity(entity_id, hidden_by=None)
        self.shown &= wanted  # no longer used (or the setting none): hidden again next time
        for entity_id in sorted(wanted - self.hidden - self.shown):
            entry = reg.async_get(entity_id)
            if entry is None or entry.hidden_by is not None:
                continue  # no registry entry, or hidden already (by hand, by its integration)
            reg.async_update_entity(entity_id, hidden_by=er.RegistryEntryHider.INTEGRATION)
            self.hidden.add(entity_id)
        if (self.hidden, self.shown) != before:
            self._save()

    @callback
    def _registry_updated(self, event: Event) -> None:
        data = event.data
        entity_id = data["entity_id"]
        if data["action"] == "update" and (old := data.get("old_entity_id")) in self.hidden:
            self.hidden.discard(old)  # renamed: the rooms follow (issues.py), so does this
            self.hidden.add(entity_id)
            self._save()
        if data["action"] == "remove" and entity_id in self.hidden | self.shown:
            self.hidden.discard(entity_id)
            self.shown.discard(entity_id)
            self._save()
        if data["action"] == "update" and "hidden_by" in data.get("changes", {}) and entity_id in self.hidden:
            entry = er.async_get(self.hass).async_get(entity_id)
            if entry is None or entry.hidden_by is not er.RegistryEntryHider.INTEGRATION:
                self.hidden.discard(entity_id)  # shown (or hidden) by hand: theirs now
                if entry is not None and entry.hidden_by is None:
                    self.shown.add(entity_id)
                self._save()
        if data["action"] == "create":
            self.changed()  # (an entity a room uses may come late)

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"hidden": sorted(self.hidden), "shown": sorted(self.shown)}, 0)


def get_hider(hass: HomeAssistant) -> Hider | None:
    return hass.data.get(DOMAIN, {}).get("hider")


async def async_setup_hider(hass: HomeAssistant) -> Callable[[], None]:
    hider = Hider(hass)
    hass.data.setdefault(DOMAIN, {})["hider"] = hider
    await hider.async_start()
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, lambda _e: hider.stop())
    return hider.stop

