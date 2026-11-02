"""What the panel shows of a room: its sources, devices and cables (the room's
setup, model.py), the route of each source through them, and its settings.

Routes are worked out by the device layer (devices/graph.py), the same
calculation that switches the room.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.const import ATTR_FRIENDLY_NAME, ATTR_SUPPORTED_FEATURES
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from ..const import CENTRAL_SETTINGS, CONF_AREA, CONF_FOLLOW_PLAYER, CONF_OCCUPANCY, DOMAIN
from ..devices.graph import route
from ..players import suggested_role
from ..devices.model import Edge, Mode, Node, Signal
from ..model import (
    DEVICE_SETTINGS,
    KIND_GIVES,
    NEEDS,
    OPT_CABLES,
    OPT_VOLUME,
    PASSIVE_KINDS,
    ROOM_SETTINGS,
    SOURCE_SETTINGS,
    device_setting,
    devices_of,
    shows_picture,
    source_setting,
    sources_of,
    switch_order,
)


def _name(hass: HomeAssistant, entity_id: str | None) -> str | None:
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    return str(state.attributes.get(ATTR_FRIENDLY_NAME) or entity_id) if state else entity_id


def routes_of(sources: list[dict], devices: list[dict], cables: list[dict]) -> dict[str, dict]:
    """The route of each source: devices on it, cables it uses (index), devices fed
    directly (no cable brings them a signal), the order they come on in and the
    input wanted on a device."""
    nodes = {d["id"]: Node(key=d["id"], entities=[], sources=(), gives=KIND_GIVES.get(d["kind"], frozenset())) for d in devices}
    ids = {s["id"] for s in sources} | set(nodes)
    edges = [
        Edge(
            source=c["from"],
            target=c["to"],
            signals=frozenset(Signal(x) for x in c.get("signals") or ()),
            input=c.get("input") or None,
            mode=Mode(c.get("mode") or Mode.BMP),
        )
        if c.get("from") in ids and c.get("to") in ids
        else Edge(source="", target="", signals=frozenset())  # keeps the indices
        for c in cables
    ]
    out: dict[str, dict] = {}
    for src in sources:
        needs = [Signal(x) for x in src.get("needs") or ()]
        r = route(src["id"], needs, nodes, edges)
        on = [d for d in devices if d["id"] in r.nodes and d["kind"] not in PASSIVE_KINDS]
        out[src["id"]] = {
            "devices": sorted(r.nodes),
            # the order they come on in: the picture's before the player, the sound's after it
            "order": switch_order(on, cables, [src["id"]]),
            "picture": sorted(d["id"] for d in on if shows_picture(d, cables)),
            "cables": sorted(r.edges),
            "direct": sorted(r.direct),
            "inputs": {k: {"input": i, "mode": str(m)} for k, (i, m) in sorted(r.inputs.items())},
        }
    return out


def _offers_onoff(hass: HomeAssistant, player: str) -> bool | None:
    state = hass.states.get(player)
    if state is None:
        return None
    return bool(int(state.attributes.get(ATTR_SUPPORTED_FEATURES, 0) or 0) & MediaPlayerEntityFeature.TURN_ON)


def setup_version(entry: ConfigEntry) -> str:
    """Changes when the room's setup or name changes (another window, an action):
    the panel does not save over it."""
    raw = json.dumps({"title": entry.title, "options": entry.options}, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def area_of(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """The room's area (id): its device's (set in Home Assistant), else the one chosen at setup."""
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    area_id = (device.area_id if device else None) or entry.options.get(CONF_AREA)
    return area_id if area_id and ar.async_get(hass).async_get_area(area_id) else None


def room_view(hass: HomeAssistant, entry: ConfigEntry, data: dict | None, layout: dict | None = None) -> dict[str, Any]:
    """A room as the panel shows it (data: its stored settings, None while not loaded)."""
    o = entry.options
    area_id = area_of(hass, entry)
    sources = [
        {
            "id": key,
            "player": s["player"],
            "name": _name(hass, s["player"]),
            "needs": [str(x) for x in NEEDS[key]],
            "host": s.get("host") or None,
            "onoff": s.get("onoff") or "auto",
            # what the player offers (the panel says what "auto" does with it)
            "offers_onoff": _offers_onoff(hass, s["player"]),
            "on_action": s.get("on_action") or None,
            "off_action": s.get("off_action") or None,
            # the settings of its hardware plug (with one, while the room is loaded)
            "settings": {k: source_setting(data, key, k) for k in SOURCE_SETTINGS} if s.get("host") and data is not None else None,
        }
        for key, s in sources_of(o).items()
        if key in NEEDS
    ]
    devices = []
    for d in devices_of(o):
        device = dict(d)
        if (inputs := d.get("inputs")) is not None:
            state = hass.states.get(inputs["entity"]) if inputs.get("entity") else None
            offered = list(state.attributes.get("source_list") or []) if state else []
            device["inputs"] = {**inputs, "list": offered or list(inputs.get("actions") or {})}
        device["settings"] = {k: device_setting(data, d, k) for k in DEVICE_SETTINGS} if data is not None else None
        devices.append(device)
    ids = {s["id"] for s in sources} | {d["id"] for d in devices}
    cables = [dict(c) for c in o.get(OPT_CABLES) or [] if c["from"] in ids and c["to"] in ids]
    return {
        "entry_id": entry.entry_id,
        "name": entry.title,
        "version": setup_version(entry),
        "loaded": entry.state is ConfigEntryState.LOADED,
        "area_id": area_id,
        "area": area.name if (area := ar.async_get(hass).async_get_area(area_id or "")) else None,
        "sources": sources,
        "devices": devices,
        "cables": cables,
        "routes": routes_of(sources, devices, cables),
        "volume": dict(o.get(OPT_VOLUME) or {"where": "players"}),
        "occupancy": list(o.get(CONF_OCCUPANCY) or []),
        "settings": {k: data.get(k, v) for k, v in ROOM_SETTINGS.items()} if data is not None else None,
        "layout": dict(layout or {}),
    }


def central_view(hass: HomeAssistant, entry: ConfigEntry, data: dict | None) -> dict[str, Any]:
    """The central entry as the panel shows it."""
    return {
        "entry_id": entry.entry_id,
        "name": entry.title,
        "follow_player": entry.options.get(CONF_FOLLOW_PLAYER) or None,
        "settings": {k: data.get(k, v) for k, v in CENTRAL_SETTINGS.items()} if data is not None else None,
    }


def player_suggestions(hass: HomeAssistant) -> dict[str, dict[str, str]]:
    """Per area, the players of known systems (Kodi, Music Assistant) a new player of
    a room in that area is proposed with: {area_id: {"video" | "audio": entity_id}}."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    found: dict[str, dict[str, str]] = {}
    for entry in ent_reg.entities.values():
        if entry.domain != "media_player" or entry.disabled or entry.platform == DOMAIN:
            continue
        area_id = entry.area_id
        if area_id is None and entry.device_id:
            device = dev_reg.async_get(entry.device_id)
            area_id = device.area_id if device else None
        if area_id and (role := suggested_role(hass, entry)) is not None:
            found.setdefault(area_id, {}).setdefault(role, entry.entity_id)
    return found
