"""What the panel shows of a room: its sources, devices and cables, and the route
of each source through them.

The room's options are read as they are today: the players are the sources, the
roles display / speakers / subwoofer are one device each, the connections are the
cables. Routes are worked out by the device layer (devices/graph.py), the same
calculation that switches the room.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import ATTR_FRIENDLY_NAME, ATTR_UNIT_OF_MEASUREMENT, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from ..const import (
    CONF_AREA,
    CONF_AUDIO_PLAYER,
    CONF_AUDIO_POWER,
    CONF_BASS,
    CONF_CONNECTIONS,
    CONF_DISPLAY,
    CONF_DISPLAY_POWER,
    CONF_FOLLOW_PLAYER,
    CONF_OCCUPANCY,
    CONF_SPEAKERS,
    CONF_VIDEO_PLAYER,
    CONF_VOLUME_CONTROL,
    CONF_VOLUME_ENTITY,
    CONN_FROM,
    CONN_INPUT,
    CONN_MODE,
    CONN_SIGNALS,
    CONN_TO,
    DEFAULTS,
    DOMAIN,
    INPUT_ROLES,
    ROLE_ACTIONS,
    SOURCE_AUDIO,
    SOURCE_CONF,
    SOURCE_VIDEO,
    SOURCES,
    VOLUME_DEVICE,
)
from ..devices.const import SWITCH_DOMAINS
from ..devices.graph import route
from ..devices.model import Edge, Mode, Node, Signal
from ..room.power import role_control, role_inputs

# a device kind puts out (picture / sound); the route ends there unless a cable goes on
KIND_GIVES: dict[str, frozenset[Signal]] = {
    "display": frozenset({Signal.PICTURE}),
    "receiver": frozenset({Signal.SOUND}),
    "amp": frozenset({Signal.SOUND}),
    "speakers": frozenset({Signal.SOUND}),
    "subwoofer": frozenset({Signal.SOUND}),
}
NEEDS = {SOURCE_VIDEO: (Signal.PICTURE, Signal.SOUND), SOURCE_AUDIO: (Signal.SOUND,)}

# today's option keys as ids in the panel
ROLE_DEVICES = ((CONF_DISPLAY, "display", CONF_DISPLAY_POWER), (CONF_SPEAKERS, "speakers", CONF_AUDIO_POWER), (CONF_BASS, "subwoofer", None))
ID_OF = {CONF_VIDEO_PLAYER: SOURCE_VIDEO, CONF_AUDIO_PLAYER: SOURCE_AUDIO, CONF_DISPLAY: "display", CONF_SPEAKERS: "speakers", CONF_BASS: "subwoofer"}


def _name(hass: HomeAssistant, entity_id: str | None) -> str | None:
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    return str(state.attributes.get(ATTR_FRIENDLY_NAME) or entity_id) if state else entity_id


def routes_of(sources: list[dict], devices: list[dict], cables: list[dict]) -> dict[str, dict]:
    """The route of each source: devices on it, cables it uses (index), devices fed
    directly (no cable reaches them) and the input wanted on a device."""
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
        out[src["id"]] = {
            "devices": sorted(r.nodes),
            "cables": sorted(r.edges),
            "direct": sorted(r.direct),
            "inputs": {k: {"input": i, "mode": str(m)} for k, (i, m) in sorted(r.inputs.items())},
        }
    return out


def _settings(hass: HomeAssistant, entry: ConfigEntry) -> list[dict[str, Any]]:
    """The entry's settings as they are set today (its configuration entities)."""
    out = []
    for ent in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id):
        if ent.entity_category != EntityCategory.CONFIG or ent.disabled:
            continue
        state = hass.states.get(ent.entity_id)
        name = str(state.attributes.get(ATTR_FRIENDLY_NAME)) if state else ent.entity_id
        name = name.removeprefix(f"{entry.title} ")  # the device's name: the room is known
        out.append(
            {
                "entity_id": ent.entity_id,
                "key": ent.translation_key or ent.unique_id.removeprefix(f"{entry.entry_id}_"),
                "name": name,
                "state": state.state if state else None,
                "unit": state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) if state else None,
            }
        )
    return sorted(out, key=lambda x: x["entity_id"])


def _area(hass: HomeAssistant, entry: ConfigEntry, options: dict) -> str | None:
    """The room's area: its device's (set in Home Assistant), else the one chosen at setup."""
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    area_id = (device.area_id if device else None) or options.get(CONF_AREA)
    area = ar.async_get(hass).async_get_area(area_id) if area_id else None
    return area.name if area else None


def room_view(hass: HomeAssistant, entry: ConfigEntry, layout: dict | None = None) -> dict[str, Any]:
    """A room as the panel shows it."""
    o: dict[str, Any] = {**DEFAULTS, **entry.data, **entry.options}
    sources = []
    for key in SOURCES:
        player_k, on_k, off_k, host_k = SOURCE_CONF[key]
        if o.get(player_k):
            sources.append(
                {
                    "id": key,
                    "player": o[player_k],
                    "name": _name(hass, o[player_k]),
                    "needs": [str(s) for s in NEEDS[key]],
                    "host": o.get(host_k) or None,
                    "on_action": o.get(on_k) or None,
                    "off_action": o.get(off_k) or None,
                }
            )
    devices = []
    for conf, kind, power_k in ROLE_DEVICES:
        entities = list(o.get(conf) or [])
        on_k, off_k = ROLE_ACTIONS[conf]
        actions = [o[on_k], o[off_k]] if o.get(on_k) and o.get(off_k) else None
        if not entities and not actions:
            continue
        device: dict[str, Any] = {
            "id": ID_OF[conf],
            "kind": kind,
            "power": {
                "plugs": [e for e in entities if e.split(".")[0] in SWITCH_DOMAINS],
                "devices": [e for e in entities if e.split(".")[0] not in SWITCH_DOMAINS],
                "actions": actions,
                "sensors": list(o.get(power_k) or []) if power_k else [],
            },
            "control": None,
            "inputs": None,
        }
        if conf in INPUT_ROLES:
            c = role_control(o, conf)
            device["control"] = {"on": str(c.on), "off": str(c.off), "fallback": c.fallback, "timeout": c.timeout}
            if (inputs := role_inputs(o, conf)) is not None:
                state = hass.states.get(inputs.entity) if inputs.entity else None
                offered = list(state.attributes.get("source_list") or []) if state else []
                device["inputs"] = {
                    "entity": inputs.entity,
                    "actions": dict(inputs.actions),
                    "pause": inputs.pause,
                    "list": offered or list(inputs.actions),
                }
        devices.append(device)
    cables = []
    for c in o.get(CONF_CONNECTIONS) or []:
        if c.get(CONN_FROM) in ID_OF and c.get(CONN_TO) in ID_OF:
            cables.append(
                {
                    "from": ID_OF[c[CONN_FROM]],
                    "to": ID_OF[c[CONN_TO]],
                    "signals": list(c.get(CONN_SIGNALS) or []),
                    "input": c.get(CONN_INPUT) or None,
                    "mode": c.get(CONN_MODE) or "bmp",
                }
            )
    ids = {s["id"] for s in sources} | {d["id"] for d in devices}
    cables = [c for c in cables if c["from"] in ids and c["to"] in ids]
    volume = (
        {"where": "device", "entity": o.get(CONF_VOLUME_ENTITY)}
        if o.get(CONF_VOLUME_CONTROL) == VOLUME_DEVICE and o.get(CONF_VOLUME_ENTITY)
        else {"where": "players"}
    )
    return {
        "entry_id": entry.entry_id,
        "name": entry.title,
        "loaded": entry.state is ConfigEntryState.LOADED,
        "area": _area(hass, entry, o),
        "sources": sources,
        "devices": devices,
        "cables": cables,
        "routes": routes_of(sources, devices, cables),
        "volume": volume,
        "occupancy": list(o.get(CONF_OCCUPANCY) or []),
        "settings": _settings(hass, entry),
        "layout": dict(layout or {}),
    }


def central_view(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """The central entry as the panel shows it."""
    return {
        "entry_id": entry.entry_id,
        "name": entry.title,
        "follow_player": entry.options.get(CONF_FOLLOW_PLAYER) or None,
        "settings": _settings(hass, entry),
    }
