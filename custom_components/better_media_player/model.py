"""A room's setup (options, entry version 4.2) and how it behaves (settings in its store).

Setup (options, a change reloads the room):

    sources   the players: {"id", "kind": "video" | "audio", "player", "host", "onoff",
              "on_action", "off_action"}; any number, one plays at a time. kind: what it
              needs (video: picture and sound, active while on; audio: sound, active while
              it plays); without one the id is the kind (rooms up to 4.2: ids video / audio).
              onoff: how the room switches the player itself (SOURCE_ONOFF);
              runs_on: another player's or a device's id the player runs on (e.g. a
              Sendspin player in Kodi): started after it, through its cables, no plug
    start_source  the source the room starts with (turned on, a wake without one); None:
              the one used last
    music_source  follow me's and the announcements' music player while none plays (with
              several music players); None: the first
    devices   what is switched or passed through: {"id", "kind", "name", "power": {"plugs",
              "devices", "actions": [on, off] | None, "sensors"}, "control": {"on", "off",
              "fallback", "timeout"}, "inputs": {"entity", "actions", "pause"} | None,
              "volume": entity | None}
    cables    {"from", "to", "signals", "input", "mode"}; ends are source or device ids
    volume    {"where": "players"} or {"where": "device", "device": <device id>}
    occupancy presence sensors (follow me); area

Behaviour (store, a change takes effect at once): the room's settings (ROOM_SETTINGS)
flat in the store, each device's (device_defaults) under "devices" → <id>, the
central entry's (CENTRAL_SETTINGS) in its own store.

Before version 4 the devices were three fixed roles (display, speakers, subwoofer)
and the behaviour was set through configuration entities; options_v4 and
migrate_store turn such a room into this model. Version 4.2: one idle timeout
for every player instead of each device's run-down (migrate_store_4_2).
"""

from __future__ import annotations

from typing import Any

from .const import (
    ANNOUNCE_PAUSE,
    ANNOUNCE_VIDEO_MODES,
    CONF_AREA,
    CONF_AUDIO_POWER,
    CONF_BASS,
    CONF_CONNECTIONS,
    CONF_DISPLAY,
    CONF_DISPLAY_POWER,
    CONF_OCCUPANCY,
    CONF_SPEAKERS,
    CONF_VOLUME_CONTROL,
    CONF_VOLUME_ENTITY,
    CONN_FROM,
    CONN_INPUT,
    CONN_MODE,
    CONN_SIGNALS,
    CONN_TO,
    ENTITY_DEFAULTS,
    HIDE_DEVICES,
    HIDE_LEVELS,
    HIDE_NONE,
    HIDE_PLAYERS,
    INPUT_ROLES,
    PRESET_DEFAULTS,
    PRESET_NONE,
    PRESETS,
    ROLE_ACTIONS,
    ROLE_AUDIO_HOST,
    ROLE_VIDEO_HOST,
    SOURCE_AUDIO,
    SOURCE_CONF,
    SOURCE_VIDEO,
    SOURCES,
    VOLUME_DEVICE,
    role_conf,
)
from .devices.const import ACTION_ID, SWITCH_DOMAINS
from .devices.model import Control, Inputs, Mode, Signal

OPT_SOURCES = "sources"
OPT_DEVICES = "devices"
OPT_CABLES = "cables"
OPT_VOLUME = "volume"

KINDS = ("display", "receiver", "amp", "speakers", "subwoofer", "switch", "mixer", "splitter")
PASSIVE_KINDS = ("mixer", "splitter")  # no power, no input to choose
# what a kind puts out: a route ends there unless a cable goes on
KIND_GIVES: dict[str, frozenset[Signal]] = {
    "display": frozenset({Signal.PICTURE}),
    "receiver": frozenset({Signal.SOUND}),
    "amp": frozenset({Signal.SOUND}),
    "speakers": frozenset({Signal.SOUND}),
    "subwoofer": frozenset({Signal.SOUND}),
}
OPT_START_SOURCE = "start_source"
OPT_MUSIC_SOURCE = "music_source"
NEEDS = {SOURCE_VIDEO: (Signal.PICTURE, Signal.SOUND), SOURCE_AUDIO: (Signal.SOUND,)}
# ids no device may have (besides the sources' own and their plugs', host_key)
RESERVED_IDS = (SOURCE_VIDEO, SOURCE_AUDIO, ROLE_VIDEO_HOST, ROLE_AUDIO_HOST)
# how the room switches a player itself (its hardware plug comes before): auto = its
# own on/off if it offers one, else its own actions; player; actions; none (never)
SOURCE_ONOFF = ("auto", "player", "actions", "none")

# --------------------------------------------------------------------- settings

# the room's settings and their first values
ROOM_SETTINGS: dict[str, Any] = {
    "idle_timeout": 30,  # min nothing plays (paused, stopped, a menu): the room goes off; 0 = never
    "night_idle_timeout": 5,  # min
    "night_max_volume": 30,  # %, 0 = no limit
    "night_fade_in": 2,  # min, back to the room volume after the night
    **{f"preset_{name}": value for name, value in PRESET_DEFAULTS.items()},  # %
    "preset_fade": 0,  # s, 0 = the volume jumps to the preset
    "power_on_preset": PRESET_NONE,
    "announce_video": ANNOUNCE_PAUSE,
    "announce_video_lower": 50,  # %
    "start_timeout": 90,  # s
    "sleep_fade": 5,  # min
    "snooze_minutes": 9,
    "presence_run_down": 2,  # min, follow me: leave after
    "volume_offset": 0,  # percentage points to follow me's level
    "prewarm": True,  # follow me: speakers on when someone comes
    "wake_detect_left": True,  # follow wake: leaving the wake room ends the wake
}

# a device's settings
DEVICE_SETTINGS = ("on_wait", "standby", "standby_w", "standby_max", "night_off")
# a player's, for the plug of its hardware (only a source with one): wait after the
# player is off before the plug goes off (its system shuts down), wait after it is on
SOURCE_SETTINGS: dict[str, Any] = {"off_wait": 2, "on_wait": 0}  # min, s


def device_defaults(kind: str) -> dict[str, Any]:
    """A device's first settings (a subwoofer is off at night)."""
    display = kind == "display"
    return {
        "on_wait": 0,  # s after switching on before the next device
        "standby": False,  # wait for standby before the plug goes off (power sensors)
        "standby_w": 5.0 if display else 3.0,  # W, at or below: standby
        "standby_max": 20 if display else 5,  # min at most
        "night_off": kind == "subwoofer",  # off in night mode
    }


# what a setting accepts: (min, max) for numbers, a list of choices, or bool
SPECS: dict[str, tuple[float, float] | list[str] | type] = {
    "idle_timeout": (0, 480),
    "night_idle_timeout": (0, 480),
    "night_max_volume": (0, 100),
    "night_fade_in": (0, 30),
    **{f"preset_{name}": (0, 100) for name in PRESETS},
    "preset_fade": (0, 30),
    "power_on_preset": [PRESET_NONE, *PRESETS],
    "announce_video": list(ANNOUNCE_VIDEO_MODES),
    "announce_video_lower": (0, 100),
    "start_timeout": (10, 600),
    "sleep_fade": (0, 60),
    "snooze_minutes": (1, 120),
    "presence_run_down": (0, 60),
    "volume_offset": (-50, 50),
    "prewarm": bool,
    "wake_detect_left": bool,
    "off_wait": (0, 120),
    "on_wait": (0, 120),
    "standby": bool,
    "standby_w": (0, 200),
    "standby_max": (1, 120),
    "night_off": bool,
    "default_volume": (1, 100),
    "hide_delay": (0, 300),
    "dynamic_controls": bool,
    "hide_used": list(HIDE_LEVELS),
}


def check_value(key: str, value: Any) -> Any:
    """The value for a setting, or ValueError (with the reason)."""
    spec = SPECS[key]
    # the reason names what it takes, without words (it stands in every language's message)
    if spec is bool:
        if isinstance(value, str) and value.lower() in _BOOL_TEXT:  # e.g. an input_boolean's state
            return _BOOL_TEXT[value.lower()]
        if not isinstance(value, bool):
            raise ValueError("true / false")
        return value
    if isinstance(spec, list):
        if value not in spec:
            raise ValueError(", ".join(spec))
        return value
    low, high = spec
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise ValueError(f"{low:g}–{high:g}")
    return int(value) if float(value).is_integer() else float(value)


_BOOL_TEXT = {"true": True, "on": True, "false": False, "off": False}


# ------------------------------------------------------------------- the setup


def shows_picture(device: dict, cables: list[dict]) -> bool:
    """A device for the picture: on before the player. One that gives it (a display),
    or carries it without being one for the sound (an HDMI switch); a receiver that
    passes the picture on to the TV is one for the sound (after the player, as ever)."""
    gives = KIND_GIVES.get(device["kind"], frozenset())
    if Signal.PICTURE in gives:
        return True
    return Signal.SOUND not in gives and any(
        c.get("to") == device["id"] and Signal.PICTURE in c.get("signals", ()) for c in cables
    )


def switch_order(devices: list[dict], cables: list[dict], sources: list[str]) -> list[str]:
    """The order in which devices come on: the picture first, then the sound;
    each from the source on (how far over the cables), then by kind."""
    ids = {d["id"] for d in devices}
    depth: dict[str, int] = {}
    todo = [(s, 0) for s in sources]
    while todo:
        at, d = todo.pop(0)
        for c in cables:
            if c.get("from") == at and c.get("to") in ids and depth.get(c["to"], 0) < d + 1 <= len(ids):
                depth[c["to"]] = d + 1
                todo.append((c["to"], d + 1))
    rank = {k: i for i, k in enumerate(KINDS)}
    return [
        d["id"]
        for d in sorted(devices, key=lambda d: (not shows_picture(d, cables), depth.get(d["id"], 1), rank.get(d["kind"], 99), d["id"]))
    ]


def control_of(device: dict) -> Control:
    c = device.get("control") or {}
    return Control(
        on=Mode(c.get("on") or Mode.BMP),
        off=Mode(c.get("off") or Mode.BMP),
        fallback=bool(c.get("fallback")),
        timeout=float(c.get("timeout") or Control().timeout),
    )


def inputs_of(device: dict) -> Inputs | None:
    i = device.get("inputs")
    if not i or (not i.get("entity") and not i.get("actions")):
        return None
    return Inputs(entity=i.get("entity") or None, actions=dict(i.get("actions") or {}), pause=float(i.get("pause") or 0))


def sources_of(options: dict) -> dict[str, dict]:
    return {s["id"]: s for s in options.get(OPT_SOURCES) or [] if s.get("player")}


def source_kind(source: dict) -> str:
    """video or audio (a room up to 4.2: its id)."""
    kind = source.get("kind") or source["id"]
    return kind if kind in SOURCES else SOURCE_AUDIO


def host_key(source: str) -> str:
    """The node of a player's hardware plug (video_host, audio_host, <id>_host)."""
    return f"{source}_host"


def devices_of(options: dict) -> list[dict]:
    return list(options.get(OPT_DEVICES) or [])


def power_entities(device: dict) -> list[str]:
    p = device.get("power") or {}
    return [*(p.get("plugs") or []), *(p.get("devices") or [])]


def volume_entity(options: dict) -> str | None:
    """The entity the room's volume is set on (None: on the players)."""
    v = options.get(OPT_VOLUME) or {}
    if v.get("where") != "device":
        return None
    return next((d.get("volume") for d in devices_of(options) if d["id"] == v.get("device")), None) or None


# ------------------------------------------------------- from version 3 (roles)

ROLE_IDS = {CONF_DISPLAY: "display", CONF_SPEAKERS: "speakers", CONF_BASS: "subwoofer"}
ROLE_POWER = {CONF_DISPLAY: CONF_DISPLAY_POWER, CONF_SPEAKERS: CONF_AUDIO_POWER, CONF_BASS: None}
END_IDS = {SOURCE_CONF[s][0]: s for s in SOURCES} | ROLE_IDS  # a connection's ends


def options_v4(old: dict) -> dict:
    """A room set up with roles (version 3 options) as devices."""
    sources = []
    for key in SOURCES:
        player_k, on_k, off_k, host_k = SOURCE_CONF[key]
        if old.get(player_k):
            sources.append(
                {
                    "id": key,
                    "player": old[player_k],
                    "host": old.get(host_k) or None,
                    "on_action": old.get(on_k) or None,
                    "off_action": old.get(off_k) or None,
                }
            )
    devices = []
    for conf, dev_id in ROLE_IDS.items():
        entities = list(old.get(conf) or [])
        on_k, off_k = ROLE_ACTIONS[conf]
        actions = [old[on_k], old[off_k]] if old.get(on_k) and old.get(off_k) else None
        if not entities and not actions:
            continue
        device: dict[str, Any] = {
            "id": dev_id,
            "kind": dev_id,
            "name": None,
            "power": {
                "plugs": [e for e in entities if e.split(".")[0] in SWITCH_DOMAINS],
                "devices": [e for e in entities if e.split(".")[0] not in SWITCH_DOMAINS],
                "actions": actions,
                "sensors": list(old.get(ROLE_POWER[conf]) or []) if ROLE_POWER[conf] else [],
            },
            "control": None,
            "inputs": None,
            "volume": None,
        }
        if conf in INPUT_ROLES:
            device["control"] = {
                "on": old.get(role_conf(conf, "power_on")) or "bmp",
                "off": old.get(role_conf(conf, "power_off")) or "bmp",
                "fallback": bool(old.get(role_conf(conf, "cec_fallback"))),
                "timeout": float(old.get(role_conf(conf, "cec_timeout")) or Control().timeout),
            }
            entity = old.get(role_conf(conf, "inputs"))
            actions_in = dict(old.get(role_conf(conf, "input_actions")) or {})
            if entity or actions_in:
                device["inputs"] = {
                    "entity": entity or None,
                    "actions": actions_in,
                    "pause": float(old.get(role_conf(conf, "input_pause")) or 0),
                }
        devices.append(device)
    ids = {s["id"] for s in sources} | {d["id"] for d in devices}
    cables = [
        {
            "from": END_IDS[c[CONN_FROM]],
            "to": END_IDS[c[CONN_TO]],
            "signals": list(c.get(CONN_SIGNALS) or []),
            "input": c.get(CONN_INPUT) or None,
            "mode": c.get(CONN_MODE) or "bmp",
        }
        for c in old.get(CONF_CONNECTIONS) or []
        if END_IDS.get(c.get(CONN_FROM)) in ids and END_IDS.get(c.get(CONN_TO)) in ids
    ]
    volume: dict[str, Any] = {"where": "players"}
    entity = old.get(CONF_VOLUME_ENTITY) if old.get(CONF_VOLUME_CONTROL) == VOLUME_DEVICE else None
    if entity:
        # the device it belongs to (its own media player), else the speakers, else the display
        owner = next((d for d in devices if entity in d["power"]["devices"]), None)
        guessed = owner is None
        if owner is None:
            owner = next((d for d in devices if d["id"] == "speakers"), None) or next(
                (d for d in devices if d["id"] == "display"), None
            )
        if owner is None:  # players only: a device of its own for the volume
            owner = {"id": "volume", "kind": "amp", "name": None, "power": None,  # (switched by nobody: passive)
                     "control": None, "inputs": None, "volume": None}
            devices.append(owner)
        owner["volume"] = entity
        if guessed:
            owner["volume_migrated"] = True  # the panel says where it was put
        volume = {"where": "device", "device": owner["id"]}
    out: dict[str, Any] = {OPT_SOURCES: sources, OPT_DEVICES: devices, OPT_CABLES: cables, OPT_VOLUME: volume}
    if old.get(CONF_OCCUPANCY):
        out[CONF_OCCUPANCY] = list(old[CONF_OCCUPANCY])
    if old.get(CONF_AREA):
        out[CONF_AREA] = old[CONF_AREA]
    return out


# the role settings of version 3 (in the store) and the devices they go to
# (the run-downs are gone with version 4.2: the idle timeout instead)
_ROLE_SETTINGS = {
    "display_on_wait": (("display", "on_wait"),),
    "audio_on_wait": (("speakers", "on_wait"),),
    "display_standby": (("display", "standby"),),
    "display_standby_w": (("display", "standby_w"),),
    "display_standby_max": (("display", "standby_max"),),
    "audio_standby": (("speakers", "standby"),),
    "audio_standby_w": (("speakers", "standby_w"),),
    "audio_standby_max": (("speakers", "standby_max"),),
}



def migrate_store(data: dict, options: dict) -> dict:
    """The room's stored values of version 3: role settings go to the devices,
    the hardware plug's to the players (as version 4.2)."""
    data = dict(data)
    for old in ("display_run_down", "audio_run_down", "night_display_run_down"):
        data.pop(old, None)
    for old, new in _RENAMED_4_2.items():
        if old in data:
            data[new] = data.pop(old)
    devices = {d["id"]: d for d in devices_of(options)}
    per_device: dict[str, dict] = {k: dict(v) for k, v in (data.get("devices") or {}).items()}
    for old, targets in _ROLE_SETTINGS.items():
        value = data.pop(old, ENTITY_DEFAULTS[old])
        for dev_id, key in targets:
            if dev_id in devices:
                per_device.setdefault(dev_id, {})[key] = value
    for dev_id in devices:
        per_device.setdefault(dev_id, {})
    data["devices"] = per_device
    # a player's hardware plug: the room's value goes to each player that has one
    hosted = [s["id"] for s in options.get(OPT_SOURCES) or [] if s.get("host")]
    per_source: dict[str, dict] = {k: dict(v) for k, v in (data.get("sources") or {}).items()}
    for old, key in (("host_run_down", "off_wait"), ("host_on_wait", "on_wait")):
        value = data.pop(old, ENTITY_DEFAULTS[old])
        for source in hosted:
            per_source.setdefault(source, {})[key] = value
    data["sources"] = per_source
    # own on/off actions had the role's id (their assumed state is kept)
    if assumed := data.get("assumed"):
        ids = {"audio": "speakers", "bass": "subwoofer"}
        data["assumed"] = {
            (f"{ACTION_ID}.{ids[k.split('.', 1)[1]]}" if k.startswith(f"{ACTION_ID}.") and k.split(".", 1)[1] in ids else k): v
            for k, v in assumed.items()
        }
    return data


# version 4.2: the idle timeout is every player's; no run-down of the devices
_RENAMED_4_2 = {"video_idle_timeout": "idle_timeout", "night_video_idle_timeout": "night_idle_timeout"}


def migrate_store_4_2(data: dict) -> dict:
    """The room's stored values of version 4.1 (only in pre-releases): the idle
    timeout's new names, the players' plug run-down is their off_wait, the
    devices' run-downs go."""
    data = dict(data)
    for old, new in _RENAMED_4_2.items():
        if old in data:
            data[new] = data.pop(old)
    data["devices"] = {
        k: {key: v for key, v in values.items() if key not in ("run_down", "night_run_down")}
        for k, values in (data.get("devices") or {}).items()
    }
    sources = {}
    for k, values in (data.get("sources") or {}).items():
        values = dict(values)
        if "run_down" in values:
            values["off_wait"] = values.pop("run_down")
        sources[k] = values
    data["sources"] = sources
    return data


def source_setting(data: dict, source: str, key: str) -> Any:
    """A player's setting for the plug of its hardware: stored, else its first value."""
    stored = (data.get("sources") or {}).get(source) or {}
    return stored.get(key, SOURCE_SETTINGS[key])


def device_setting(data: dict, device: dict, key: str) -> Any:
    """A device's setting: stored, else its kind's first value."""
    stored = (data.get("devices") or {}).get(device["id"]) or {}
    return stored[key] if key in stored else device_defaults(device["kind"])[key]


# ------------------------------------------------------------ entities in a setup


def players_of(options: dict) -> list[str]:
    return [s["player"] for s in options.get(OPT_SOURCES) or [] if s.get("player")]


def switched_of(options: dict) -> list[str]:
    """What the room switches or plays on (each belongs to one room): players, their
    plugs, the devices' power entities, the volume's entity."""
    out = list(players_of(options))
    out += [s["host"] for s in options.get(OPT_SOURCES) or [] if s.get("host")]
    for d in devices_of(options):
        out += power_entities(d)
    if (entity := volume_entity(options)) is not None:
        out.append(entity)
    return list(dict.fromkeys(out))


def entities_of(options: dict) -> list[str]:
    """Every entity the room is set up with."""
    out = switched_of(options)
    for s in options.get(OPT_SOURCES) or []:
        out += [a for a in (s.get("on_action"), s.get("off_action")) if a]
    for d in devices_of(options):
        p = d.get("power") or {}
        out += list(p.get("actions") or []) + list(p.get("sensors") or [])
        i = d.get("inputs") or {}
        out += [e for e in [i.get("entity"), *(i.get("actions") or {}).values()] if e]
        if d.get("volume"):
            out.append(d["volume"])
    out += list(options.get(CONF_OCCUPANCY) or [])
    return list(dict.fromkeys(out))


def used_by_level(options: dict, level: str) -> list[str]:
    """What the central setting hide_used hides of a room (hide.py): its players;
    also the devices' media players; everything it is set up with but its
    presence sensors (they serve more than the room)."""
    if level == HIDE_NONE:
        return []
    if level == HIDE_PLAYERS:
        return players_of(options)
    presence = set(options.get(CONF_OCCUPANCY) or [])
    used = [e for e in entities_of(options) if e not in presence]
    if level == HIDE_DEVICES:
        return [e for e in used if e.split(".", 1)[0] == "media_player"]
    return used  # HIDE_ALL


def renamed(value: Any, old: str, new: str) -> Any:
    """The setup with an entity's old id replaced by its new one (anywhere in it)."""
    if value == old:
        return new
    if isinstance(value, list):
        return [renamed(v, old, new) for v in value]
    if isinstance(value, dict):
        return {k: renamed(v, old, new) for k, v in value.items()}
    return value


# setting entities up to version 3 (removed with version 4: set_setting / get_settings)
REMOVED_ROOM_ENTITIES = (
    ("number", "display_run_down"), ("number", "audio_run_down"), ("number", "host_run_down"),
    ("number", "video_idle_timeout"), ("number", "night_video_idle_timeout"), ("number", "night_display_run_down"),
    ("number", "night_max_volume"), *(("number", f"preset_{name}") for name in PRESETS), ("number", "preset_fade"),
    ("number", "announce_video_lower"), ("number", "start_timeout"), ("number", "display_on_wait"),
    ("number", "audio_on_wait"), ("number", "host_on_wait"), ("number", "display_standby_w"),
    ("number", "display_standby_max"), ("number", "audio_standby_w"), ("number", "audio_standby_max"),
    ("number", "sleep_fade"), ("number", "snooze_minutes"), ("number", "night_fade_in"),
    ("number", "presence_run_down"), ("number", "volume_offset"),
    ("select", "power_on_preset"), ("select", "announce_video"),
    ("switch", "display_standby"), ("switch", "audio_standby"), ("switch", "prewarm"), ("switch", "wake_detect_left"),
)
REMOVED_CENTRAL_ENTITIES = (("number", "default_volume"), ("number", "hide_delay"), ("switch", "dynamic_controls"))
