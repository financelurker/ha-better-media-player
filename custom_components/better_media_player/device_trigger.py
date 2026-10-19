"""Device triggers of a room: its source changes (see source_event.py)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_PLATFORM, CONF_TYPE
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import CONF_ENTRY_TYPE, DOMAIN, ENTRY_TYPE_HUB, EVENT_SOURCE_CHANGED, SOURCE_AUDIO, SOURCE_VIDEO

# trigger type -> the source it waits for (None: any change)
TRIGGERS: dict[str, str | None] = {
    "source_changed": None,
    "source_video": SOURCE_VIDEO,
    "source_audio": SOURCE_AUDIO,
    "source_off": "off",
}

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend({vol.Required(CONF_TYPE): vol.In(TRIGGERS)})


def _entry_id(hass: HomeAssistant, device_id: str) -> str | None:
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return None
    for domain, ident in device.identifiers:
        if domain == DOMAIN:
            entry = hass.config_entries.async_get_entry(ident)
            if entry is not None and entry.data.get(CONF_ENTRY_TYPE) != ENTRY_TYPE_HUB:
                return ident
    return None


async def async_get_triggers(hass: HomeAssistant, device_id: str) -> list[dict[str, Any]]:
    entry_id = _entry_id(hass, device_id)
    if entry_id is None:
        return []
    entry = hass.config_entries.async_get_entry(entry_id)
    sources = getattr(entry.runtime_data, "sources", {}) if entry is not None else {}
    types = [t for t, src in TRIGGERS.items() if src in (None, "off") or src in sources]
    return [
        {CONF_PLATFORM: "device", CONF_DOMAIN: DOMAIN, CONF_DEVICE_ID: device_id, CONF_TYPE: t} for t in types
    ]


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    entry_id = _entry_id(hass, config[CONF_DEVICE_ID])
    event_data: dict[str, Any] = {"entry_id": entry_id}
    wanted = TRIGGERS[config[CONF_TYPE]]
    if wanted == "off":
        event_data["reason"] = "off"
    elif wanted is not None:
        event_data["source"] = wanted
    event_config = event_trigger.TRIGGER_SCHEMA(
        {
            event_trigger.CONF_PLATFORM: "event",
            event_trigger.CONF_EVENT_TYPE: EVENT_SOURCE_CHANGED,
            event_trigger.CONF_EVENT_DATA: event_data,
        }
    )
    return await event_trigger.async_attach_trigger(hass, event_config, action, trigger_info, platform_type="device")
