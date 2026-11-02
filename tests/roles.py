"""The tests describe rooms with the roles of version 3 (room_options in conftest):
a room's setup seen as those roles, to read it back or change it in their terms."""

from __future__ import annotations

from typing import Any

from custom_components.better_media_player.const import (
    CONF_AREA,
    CONF_CONNECTIONS,
    CONF_OCCUPANCY,
    CONF_VOLUME_CONTROL,
    CONF_VOLUME_ENTITY,
    CONN_FROM,
    CONN_INPUT,
    CONN_MODE,
    CONN_SIGNALS,
    CONN_TO,
    INPUT_ROLES,
    ROLE_ACTIONS,
    SOURCE_CONF,
    VOLUME_DEVICE,
    role_conf,
)
from custom_components.better_media_player.devices.model import Control
from custom_components.better_media_player.model import (
    END_IDS,
    OPT_CABLES,
    OPT_SOURCES,
    ROLE_IDS,
    ROLE_POWER,
    devices_of,
    volume_entity,
)


def options_v3(options: dict) -> dict:
    """The roles' view of a room (for the setup dialog until the panel edits; only
    what the roles can hold: the display, speakers and subwoofer devices)."""
    old: dict[str, Any] = {}
    for s in options.get(OPT_SOURCES) or []:
        player_k, on_k, off_k, host_k = SOURCE_CONF[s["id"]]
        old[player_k] = s["player"]
        for k, v in ((host_k, s.get("host")), (on_k, s.get("on_action")), (off_k, s.get("off_action"))):
            if v:
                old[k] = v
    conf_of = {v: k for k, v in ROLE_IDS.items()}
    for d in devices_of(options):
        conf = conf_of.get(d["id"])
        if conf is None:
            continue
        p = d.get("power") or {}
        entities = [*(p.get("plugs") or []), *(p.get("devices") or [])]
        if entities:
            old[conf] = entities
        if p.get("actions"):
            on_k, off_k = ROLE_ACTIONS[conf]
            old[on_k], old[off_k] = p["actions"]
        if ROLE_POWER[conf] and p.get("sensors"):
            old[ROLE_POWER[conf]] = list(p["sensors"])
        if conf in INPUT_ROLES:
            c = d.get("control") or {}
            for key, value in (("power_on", c.get("on")), ("power_off", c.get("off"))):
                if value and value != "bmp":
                    old[role_conf(conf, key)] = value
            if c.get("fallback"):
                old[role_conf(conf, "cec_fallback")] = True
            if c.get("timeout") and float(c["timeout"]) != Control().timeout:
                old[role_conf(conf, "cec_timeout")] = c["timeout"]
            i = d.get("inputs") or {}
            if i.get("entity"):
                old[role_conf(conf, "inputs")] = i["entity"]
            if i.get("actions"):
                old[role_conf(conf, "input_actions")] = dict(i["actions"])
            if i.get("pause"):
                old[role_conf(conf, "input_pause")] = i["pause"]
    end_of = {v: k for k, v in END_IDS.items()}
    connections = [
        {CONN_FROM: end_of[c["from"]], CONN_TO: end_of[c["to"]], CONN_SIGNALS: list(c["signals"]),
         CONN_INPUT: c.get("input"), CONN_MODE: c.get("mode") or "bmp"}
        for c in options.get(OPT_CABLES) or []
        if c["from"] in end_of and c["to"] in end_of
    ]
    if connections:
        old[CONF_CONNECTIONS] = connections
    entity = volume_entity(options)
    old[CONF_VOLUME_CONTROL] = VOLUME_DEVICE if entity else "players"
    if entity:
        old[CONF_VOLUME_ENTITY] = entity
    if options.get(CONF_OCCUPANCY):
        old[CONF_OCCUPANCY] = list(options[CONF_OCCUPANCY])
    if options.get(CONF_AREA):
        old[CONF_AREA] = options[CONF_AREA]
    return old
