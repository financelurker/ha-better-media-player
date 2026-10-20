"""Device triggers of a room: its source changes (see source_event.py) and its
night mode (see night.py)."""

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

from .const import (
    CONF_AUDIO_PLAYER,
    CONF_ENTRY_TYPE,
    CONF_VIDEO_PLAYER,
    DOMAIN,
    ENTRY_TYPE_HUB,
    EVENT_NIGHT_MODE,
    EVENT_SOURCE_CHANGED,
    SOURCE_AUDIO,
    SOURCE_VIDEO,
)

# trigger type -> its event and the event data it waits for
TRIGGERS: dict[str, tuple[str, dict[str, Any]]] = {
    "source_changed": (EVENT_SOURCE_CHANGED, {}),
    "source_video": (EVENT_SOURCE_CHANGED, {"source": SOURCE_VIDEO}),
    "source_audio": (EVENT_SOURCE_CHANGED, {"source": SOURCE_AUDIO}),
    "source_off": (EVENT_SOURCE_CHANGED, {"reason": "off"}),
    "night_mode_on": (EVENT_NIGHT_MODE, {"night_mode": True}),
    "night_mode_off": (EVENT_NIGHT_MODE, {"night_mode": False}),
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
    options = entry.options if entry is not None else {}
    # from the options: also for a room that is not loaded right now
    sources = {k for k, conf in ((SOURCE_VIDEO, CONF_VIDEO_PLAYER), (SOURCE_AUDIO, CONF_AUDIO_PLAYER)) if options.get(conf)}
    types = [t for t, (_event, data) in TRIGGERS.items() if data.get("source") in (None, *sources)]
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
    event, data = TRIGGERS[config[CONF_TYPE]]
    event_config = event_trigger.TRIGGER_SCHEMA(
        {
            event_trigger.CONF_PLATFORM: "event",
            event_trigger.CONF_EVENT_TYPE: event,
            event_trigger.CONF_EVENT_DATA: {"entry_id": entry_id, **data},
        }
    )
    return await event_trigger.async_attach_trigger(hass, event_config, action, trigger_info, platform_type="device")
