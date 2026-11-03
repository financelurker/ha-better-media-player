"""Editing in the panel: a room's draft (setup and settings) checked and saved.

A draft is what the panel edits: the setup as in the options (model.py) and the
settings of the room, its devices and its players' hardware plugs. check_room
returns the problems the panel shows: errors stop saving, hints do not.
"""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol

from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.const import ATTR_SUPPORTED_FEATURES
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from ..const import (
    ACTION_DOMAINS,
    CONF_AREA,
    CONF_ENTRY_TYPE,
    CONF_OCCUPANCY,
    DOMAIN,
    ENTRY_TYPE_HUB,
    MODES,
    SIGNALS,
    SOURCE_AUDIO,
    SOURCE_VIDEO,
    VOLUME_ENTITY_DOMAINS,
)
from ..devices.const import DEVICE_DOMAINS, SWITCH_DOMAINS
from ..devices.graph import cycles
from ..devices.model import Edge, Signal
from ..model import (
    DEVICE_SETTINGS,
    KINDS,
    NEEDS,
    OPT_CABLES,
    OPT_DEVICES,
    OPT_SOURCES,
    OPT_VOLUME,
    PASSIVE_KINDS,
    RESERVED_IDS,
    ROOM_SETTINGS,
    SOURCE_ONOFF,
    SOURCE_SETTINGS,
    OPT_MUSIC_SOURCE,
    OPT_START_SOURCE,
    check_value,
    host_key,
    source_kind,
    power_entities,
    switched_of,
)
from ..players import is_group
from .view import routes_of

ID_PATTERN = r"^[a-z][a-z0-9_]{0,31}$"

_ACTION = vol.Any(None, vol.All(cv.entity_id, cv.entity_domain(ACTION_DOMAINS)))


def _of(domains: list[str]):
    return vol.All(vol.Any(None, [vol.All(cv.entity_id, cv.entity_domain(domains))]), lambda v: list(v or []))

SOURCE_SCHEMA = vol.Schema(
    {
        vol.Required("id"): str,
        vol.Optional("kind", default=None): vol.Any(None, vol.In([SOURCE_VIDEO, SOURCE_AUDIO])),
        vol.Required("player"): vol.All(cv.entity_id, cv.entity_domain(["media_player"])),
        vol.Optional("host", default=None): vol.Any(None, vol.All(cv.entity_id, cv.entity_domain(SWITCH_DOMAINS))),
        vol.Optional("onoff", default="auto"): vol.In(SOURCE_ONOFF),
        vol.Optional("on_action", default=None): _ACTION,
        vol.Optional("off_action", default=None): _ACTION,
    }
)
DEVICE_SCHEMA = vol.Schema(
    {
        vol.Required("id"): str,
        vol.Required("kind"): vol.In(KINDS),
        vol.Optional("name", default=None): vol.Any(None, vol.All(str, vol.Length(max=60))),
        vol.Optional("power", default=None): vol.Any(
            None,
            vol.Schema(
                {
                    vol.Optional("plugs", default=list): _of(SWITCH_DOMAINS),
                    vol.Optional("devices", default=list): _of(DEVICE_DOMAINS),
                    vol.Optional("actions", default=None): vol.Any(None, vol.All([_ACTION], vol.Length(min=2, max=2))),
                    vol.Optional("sensors", default=list): _of(["sensor"]),
                }
            ),
        ),
        vol.Optional("control", default=None): vol.Any(
            None,
            vol.Schema(
                {
                    vol.Optional("on", default="bmp"): vol.In(MODES),
                    vol.Optional("off", default="bmp"): vol.In(MODES),
                    vol.Optional("fallback", default=False): bool,
                    vol.Optional("timeout", default=15.0): vol.All(vol.Coerce(float), vol.Range(min=1, max=120)),
                }
            ),
        ),
        vol.Optional("inputs", default=None): vol.Any(
            None,
            vol.Schema(
                {
                    vol.Optional("entity", default=None): vol.Any(None, vol.All(cv.entity_id, cv.entity_domain(DEVICE_DOMAINS))),
                    vol.Optional("actions", default=dict): {str: vol.All(cv.entity_id, cv.entity_domain(ACTION_DOMAINS))},
                    vol.Optional("pause", default=0.0): vol.All(vol.Coerce(float), vol.Range(min=0, max=30)),
                }
            ),
        ),
        vol.Optional("volume", default=None): vol.Any(None, vol.All(cv.entity_id, cv.entity_domain(VOLUME_ENTITY_DOMAINS))),
        vol.Optional("volume_migrated"): bool,
    }
)
CABLE_SCHEMA = vol.Schema(
    {
        vol.Required("from"): str,
        vol.Required("to"): str,
        vol.Required("signals"): vol.All([vol.In(SIGNALS)], vol.Length(min=1)),
        vol.Optional("input", default=None): vol.Any(None, vol.All(str, vol.Length(min=1))),
        vol.Optional("mode", default="bmp"): vol.In(MODES),
    }
)
SETUP_SCHEMA = vol.Schema(
    {
        vol.Required(OPT_SOURCES): [SOURCE_SCHEMA],
        vol.Required(OPT_DEVICES): [DEVICE_SCHEMA],
        vol.Required(OPT_CABLES): [CABLE_SCHEMA],
        vol.Required(OPT_VOLUME): vol.Any(
            {vol.Required("where"): "players"},
            {vol.Required("where"): "device", vol.Required("device"): str},
        ),
        vol.Optional(CONF_OCCUPANCY, default=list): _of(["binary_sensor"]),
        vol.Optional(CONF_AREA, default=None): vol.Any(None, str),
        vol.Optional(OPT_START_SOURCE, default=None): vol.Any(None, str),
        vol.Optional(OPT_MUSIC_SOURCE, default=None): vol.Any(None, str),
    }
)
SETTINGS_SCHEMA = vol.Schema(
    {
        vol.Optional("room", default=dict): {str: vol.Any(None, bool, int, float, str)},
        vol.Optional("devices", default=dict): {str: {str: vol.Any(None, bool, int, float, str)}},
        vol.Optional("sources", default=dict): {str: {str: vol.Any(None, bool, int, float, str)}},
    }
)


def clean_setup(setup: dict) -> dict:
    """The setup as it is stored: what a kind cannot have is left out."""
    out = SETUP_SCHEMA(setup)
    for s in out[OPT_SOURCES]:
        s["kind"] = source_kind(s)  # (stored, so the id can be anything)
    for d in out[OPT_DEVICES]:
        if d["kind"] in PASSIVE_KINDS:
            d["power"], d["control"], d["inputs"], d["volume"] = None, None, None, None
        if d["inputs"] is not None and not d["inputs"]["entity"] and not d["inputs"]["actions"]:
            d["inputs"] = None
    for key in (CONF_AREA, OPT_START_SOURCE, OPT_MUSIC_SOURCE):
        if not out[key]:
            out.pop(key)
    return out


def _problem(error: bool, code: str, **params: Any) -> dict[str, Any]:
    return {"error": error, "code": code, **params}


def _supports(hass: HomeAssistant, entity_id: str, feature: MediaPlayerEntityFeature) -> bool | None:
    state = hass.states.get(entity_id)
    if state is None:
        return None  # not known right now: nothing to say
    return bool(int(state.attributes.get(ATTR_SUPPORTED_FEATURES, 0) or 0) & feature)


def _own(hass: HomeAssistant, entity_id: str) -> bool:
    """An entity of Better Media Player itself (a room player: it would loop)."""
    entry = er.async_get(hass).async_get(entity_id)
    return entry is not None and entry.platform == DOMAIN


def check_room(hass: HomeAssistant, entry_id: str | None, setup: dict, settings: dict | None = None) -> list[dict]:
    """The problems of a room's draft (setup already through SETUP_SCHEMA)."""
    out: list[dict] = []
    err = lambda code, **p: out.append(_problem(True, code, **p))  # noqa: E731
    hint = lambda code, **p: out.append(_problem(False, code, **p))  # noqa: E731
    sources, devices, cables = setup[OPT_SOURCES], setup[OPT_DEVICES], setup[OPT_CABLES]

    # players
    if not sources:
        err("no_player")
    keys = [s["id"] for s in sources]
    for key in keys:
        if not re.match(ID_PATTERN, key) or key.endswith("_host"):
            err("bad_id", device=key)
        elif keys.count(key) > 1:
            err("dup_id", device=key)
    for s in sources:
        if s["id"] in (SOURCE_VIDEO, SOURCE_AUDIO) and source_kind(s) != s["id"]:
            err("source_kind_fixed", source=s["id"])  # (its triggers and actions mean that kind)
    for key in (OPT_START_SOURCE, OPT_MUSIC_SOURCE):
        if setup.get(key) and setup[key] not in keys:
            err("source_missing", source=setup[key])
    if (music := setup.get(OPT_MUSIC_SOURCE)) and any(s["id"] == music and source_kind(s) != SOURCE_AUDIO for s in sources):
        err("music_source_not_music", source=music)
    players = [s["player"] for s in sources]
    if len(set(players)) != len(players):
        err("same_player", entity=players[0])
    for s in sources:
        if is_group(hass, s["player"]):
            err("group_player", entity=s["player"])
        for e in (s["player"], s["host"]):
            if e and _own(hass, e):
                err("room_player", entity=e)
        if s["onoff"] == "actions" and not s["on_action"] and not s["off_action"]:
            err("needs_actions", source=s["id"])
        if s["onoff"] == "player" and _supports(hass, s["player"], MediaPlayerEntityFeature.TURN_ON) is False:
            hint("player_no_onoff", entity=s["player"])

    # devices
    ids = [d["id"] for d in devices]
    for d in devices:
        if not re.match(ID_PATTERN, d["id"]):
            err("bad_id", device=d["id"])
        elif d["id"] in RESERVED_IDS or d["id"] in keys or d["id"] in {host_key(k) for k in keys}:
            err("reserved_id", device=d["id"])
        elif ids.count(d["id"]) > 1:
            err("dup_id", device=d["id"])
        p = d["power"] or {}
        if d["power"] is not None and not (p["plugs"] or p["devices"] or p["actions"]):
            err("nothing_switches", device=d["id"])  # (passive: no power at all)
        if p.get("actions") and not all(p["actions"]):
            err("needs_both_actions", device=d["id"])
        for e in power_entities(d):
            if e in players:
                err("device_is_player", device=d["id"], entity=e)
            elif _own(hass, e):
                err("room_player", entity=e)
    # one entity switched by two of the room's devices or plugs
    seen: dict[str, str] = {}
    for owner, entities in [*((s["id"], [s["host"]] if s["host"] else []) for s in sources), *((d["id"], power_entities(d)) for d in devices)]:
        for e in entities:
            if e in seen and seen[e] != owner:
                err("used_twice", entity=e)
            seen.setdefault(e, owner)
    # what the room switches belongs to it alone
    mine = set(switched_of(setup))
    for other in hass.config_entries.async_entries(DOMAIN):
        if other.entry_id == entry_id or other.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_HUB:
            continue
        for e in sorted(mine & set(switched_of(other.options))):
            err("taken", entity=e, room=other.title)

    # volume
    v = setup[OPT_VOLUME]
    if v["where"] == "device":
        dev = next((d for d in devices if d["id"] == v["device"]), None)
        if dev is None or not dev["volume"]:
            err("volume_device_missing")
    for d in devices:
        if not (entity := d["volume"]):
            continue
        if entity in players:
            err("volume_is_player", entity=entity)
        elif _own(hass, entity):
            err("room_player", entity=entity)
        elif is_group(hass, entity):
            err("group_player", entity=entity)
        elif entity.startswith("media_player.") and _supports(hass, entity, MediaPlayerEntityFeature.VOLUME_SET) is False:
            err("no_volume", entity=entity)
    if v["where"] == "players":
        for s in sources:
            if _supports(hass, s["player"], MediaPlayerEntityFeature.VOLUME_SET) is False:
                hint("player_no_volume", entity=s["player"])

    # cables
    ends = set(keys) | set(ids)
    for c in cables:
        if c["from"] == c["to"]:
            err("cable_to_itself", device=c["from"])
        elif c["from"] not in ends or c["to"] not in ends or c["to"] in keys:
            err("cable_end_missing")
    edges = [
        Edge(c["from"], c["to"], frozenset(Signal(x) for x in c["signals"]), c["input"])
        for c in cables
        if c["from"] in ends and c["to"] in ends
    ]
    if cycles(edges):
        err("cycle")
    by_id = {d["id"]: d for d in devices}
    for key in keys:
        wanted: dict[str, set[str]] = {}
        for signal in NEEDS[source_kind(next(s for s in sources if s["id"] == key))]:
            reached, todo = {key}, [key]
            while todo:
                at = todo.pop()
                for e in edges:
                    if e.source == at and signal in e.signals:
                        if e.input:
                            wanted.setdefault(e.target, set()).add(e.input)
                        if e.target not in reached:
                            reached.add(e.target)
                            todo.append(e.target)
        for target, inputs in wanted.items():
            if len(inputs) > 1:
                err("input_conflict", device=target, source=key)
    for c in cables:
        target = by_id.get(c["to"])
        if not c["input"] or target is None:
            continue
        inputs = target["inputs"]
        if inputs is None:
            hint("no_inputs", device=target["id"], input=c["input"])
            continue
        offered = list(inputs["actions"])
        state = hass.states.get(inputs["entity"]) if inputs["entity"] else None
        if state is not None:
            offered += list(state.attributes.get("source_list") or [])
        if (state is not None or not inputs["entity"]) and c["input"] not in offered:
            hint("input_not_offered", device=target["id"], input=c["input"])

    # devices on no player's way switch for nothing
    if sources and not any(p["error"] for p in out if p["code"] in ("cycle", "cable_end_missing")):
        routed = set()
        for r in routes_of([{**s, "needs": [str(x) for x in NEEDS[source_kind(s)]]} for s in sources], devices, cables).values():
            routed |= set(r["devices"])
        for d in devices:
            if d["id"] not in routed:
                hint("off_route", device=d["id"])

    # settings
    if settings:
        for key, value in settings["room"].items():
            _check_setting(out, key, value, ROOM_SETTINGS)
        for dev_id, values in settings["devices"].items():
            for key, value in values.items():
                _check_setting(out, key, value, DEVICE_SETTINGS, device=dev_id)
        for src, values in settings["sources"].items():
            for key, value in values.items():
                _check_setting(out, key, value, SOURCE_SETTINGS, device=src)
    return out


def _check_setting(out: list[dict], key: str, value: Any, known, **where: str) -> None:
    if key not in known:
        out.append(_problem(True, "unknown_setting", setting=key, **where))
        return
    try:
        check_value(key, value)
    except ValueError as err:
        out.append(_problem(True, "bad_setting", setting=key, expected=str(err), **where))


def has_errors(problems: list[dict]) -> bool:
    return any(p["error"] for p in problems)
