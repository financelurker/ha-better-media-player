"""Setup (one entry per room) and options."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import ATTR_FRIENDLY_NAME, ATTR_SUPPORTED_FEATURES, CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector as sel
from homeassistant.data_entry_flow import section

from .const import (
    ACTION_DOMAINS,
    ATTR_MASS_PLAYER_TYPE,
    CONF_AREA,
    CONF_AUDIO_HOST,
    CONF_AUDIO_OFF_ACTION,
    CONF_AUDIO_ON_ACTION,
    CONF_AUDIO_PLAYER,
    CONF_AUDIO_POWER,
    CONF_BASS,
    CONF_DISPLAY,
    CONF_DISPLAY_POWER,
    CONF_ENTRY_TYPE,
    CONF_FOLLOW_PLAYER,
    CONF_OCCUPANCY,
    CONF_SPEAKERS,
    CONF_VIDEO_HOST,
    CONF_VIDEO_OFF_ACTION,
    CONF_VIDEO_ON_ACTION,
    CONF_VIDEO_PLAYER,
    CONF_VOLUME_CONTROL,
    CONF_VOLUME_ENTITY,
    DEFAULTS,
    DOMAIN,
    ENTRY_TYPE_HUB,
    ENTRY_TYPE_ROOM,
    MASS_TYPE_GROUP,
    SWITCH_DOMAINS,
    VOLUME_DEVICE,
    VOLUME_ENTITY_DOMAINS,
    VOLUME_PLAYERS,
)

VIDEO_PLATFORMS = ("kodi",)
AUDIO_PLATFORMS = ("music_assistant",)


def _entity(domain: str | list[str], device_class: str | None = None, multiple: bool = False):
    cfg: dict[str, Any] = {"domain": domain, "multiple": multiple}
    if device_class:
        cfg["device_class"] = device_class
    return sel.EntitySelector(sel.EntitySelectorConfig(**cfg))


def _num(minimum: float, maximum: float, step: float = 1, unit: str | None = None):
    return sel.NumberSelector(
        sel.NumberSelectorConfig(
            min=minimum, max=maximum, step=step, unit_of_measurement=unit, mode=sel.NumberSelectorMode.BOX
        )
    )


def schema_room(with_name: bool) -> vol.Schema:
    fields: dict = {}
    if with_name:
        fields[vol.Required(CONF_NAME)] = sel.TextSelector()
    fields[vol.Optional(CONF_AREA)] = sel.AreaSelector()
    return vol.Schema(fields)


def _fields() -> dict[str, Any]:
    """Every room option: its selector (all of them choose what is connected)."""
    action = _entity(ACTION_DOMAINS)
    plug = _entity(SWITCH_DOMAINS)
    plugs = _entity(SWITCH_DOMAINS, multiple=True)
    power = _entity("sensor", "power", multiple=True)
    return {
        CONF_VIDEO_PLAYER: _entity("media_player"),
        CONF_AUDIO_PLAYER: _entity("media_player"),
        CONF_VIDEO_ON_ACTION: action,
        CONF_VIDEO_OFF_ACTION: action,
        CONF_VIDEO_HOST: plug,
        CONF_AUDIO_ON_ACTION: action,
        CONF_AUDIO_OFF_ACTION: action,
        CONF_AUDIO_HOST: plug,
        CONF_DISPLAY: plugs,
        CONF_SPEAKERS: plugs,
        CONF_BASS: plugs,
        CONF_VOLUME_CONTROL: sel.SelectSelector(
            sel.SelectSelectorConfig(
                options=[VOLUME_PLAYERS, VOLUME_DEVICE], translation_key=CONF_VOLUME_CONTROL, mode=sel.SelectSelectorMode.LIST
            )
        ),
        CONF_VOLUME_ENTITY: _entity(VOLUME_ENTITY_DOMAINS),
        CONF_DISPLAY_POWER: power,
        CONF_AUDIO_POWER: power,
        CONF_OCCUPANCY: _entity("binary_sensor", multiple=True),
    }


# step -> its fields, and its (collapsed) sections with their fields. How the
# room behaves (times, waits, standby, follow me) is set on its device.
LAYOUT: dict[str, tuple[list[str], dict[str, list[str]]]] = {
    "players": (
        [CONF_VIDEO_PLAYER, CONF_AUDIO_PLAYER],
        {
            "special": [
                CONF_VIDEO_ON_ACTION, CONF_VIDEO_OFF_ACTION, CONF_VIDEO_HOST,
                CONF_AUDIO_ON_ACTION, CONF_AUDIO_OFF_ACTION, CONF_AUDIO_HOST,
            ]
        },
    ),
    "devices": (
        [CONF_DISPLAY, CONF_SPEAKERS, CONF_BASS, CONF_VOLUME_CONTROL, CONF_VOLUME_ENTITY],
        {"standby": [CONF_DISPLAY_POWER, CONF_AUDIO_POWER], "follow": [CONF_OCCUPANCY]},
    ),
}


def _marker(key: str):
    return vol.Optional(key, default=DEFAULTS[key]) if key in DEFAULTS else vol.Optional(key)


def schema_step(step_id: str) -> vol.Schema:
    if step_id == "room":
        return schema_room(False)
    fields = _fields()
    main, sections = LAYOUT[step_id]
    schema: dict = {_marker(k): fields[k] for k in main}
    for name, keys in sections.items():
        schema[vol.Required(name)] = section(vol.Schema({_marker(k): fields[k] for k in keys}), {"collapsed": True})
    return vol.Schema(schema)


def _step_keys(step_id: str) -> list[str]:
    if step_id == "room":
        return [CONF_AREA]
    main, sections = LAYOUT[step_id]
    return [*main, *(k for keys in sections.values() for k in keys)]


def _nested(step_id: str, options: dict) -> dict:
    """Options as the step's form shows them (sections nested)."""
    if step_id == "room":
        return dict(options)
    main, sections = LAYOUT[step_id]
    out = {k: options[k] for k in main if k in options}
    for name, keys in sections.items():
        out[name] = {k: options[k] for k in keys if k in options}
    return out


def _flat(step_id: str, user_input: dict) -> dict:
    out = dict(user_input)
    if step_id != "room":
        for name in LAYOUT[step_id][1]:
            out.update(out.pop(name, None) or {})
    return out


def _form_errors(step_id: str, errors: dict[str, str]) -> dict[str, str]:
    """Errors of the step's fields; one in a section is shown at the section."""
    if step_id == "room":
        return {k: v for k, v in errors.items() if k in _step_keys(step_id)}
    main, sections = LAYOUT[step_id]
    out = {k: v for k, v in errors.items() if k in main}
    for name, keys in sections.items():
        for k in keys:
            if k in errors:
                out.setdefault(name, errors[k])
    return out


def schema_hub() -> vol.Schema:
    return vol.Schema({vol.Optional(CONF_FOLLOW_PLAYER): _entity("media_player")})


def _validate_hub(hass: HomeAssistant, options: dict) -> dict[str, str]:
    state = hass.states.get(options.get(CONF_FOLLOW_PLAYER) or "")
    if state is not None and state.attributes.get(ATTR_MASS_PLAYER_TYPE) != MASS_TYPE_GROUP:
        return {CONF_FOLLOW_PLAYER: "not_a_group"}
    return {}


STEPS = ["room", "players", "devices"]


def _merge(options: dict, step_id: str, user_input: dict) -> dict:
    """Take a step's input; drop optional fields that were cleared."""
    keys = _step_keys(step_id)
    out = {k: v for k, v in options.items() if k not in keys}
    out.update({k: v for k, v in _flat(step_id, user_input).items() if v not in (None, "", [])})
    return out


def _validate(hass: HomeAssistant, options: dict) -> dict[str, str]:
    errors: dict[str, str] = {}
    video = options.get(CONF_VIDEO_PLAYER)
    audio = options.get(CONF_AUDIO_PLAYER)
    if not video and not audio:
        errors[CONF_VIDEO_PLAYER] = "no_player"
    if video and video == audio:
        errors[CONF_AUDIO_PLAYER] = "same_player"
    for key in (CONF_VIDEO_PLAYER, CONF_AUDIO_PLAYER):
        state = hass.states.get(options[key]) if options.get(key) else None
        if state is not None and state.attributes.get(ATTR_MASS_PLAYER_TYPE) == MASS_TYPE_GROUP:
            errors[key] = "group_player"
    if options.get(CONF_VOLUME_CONTROL) == VOLUME_DEVICE:
        device = options.get(CONF_VOLUME_ENTITY)
        state = hass.states.get(device) if device else None
        if not device:
            errors[CONF_VOLUME_ENTITY] = "needs_volume_entity"
        elif device in (video, audio):
            errors[CONF_VOLUME_ENTITY] = "volume_is_player"
        elif (reg := er.async_get(hass).async_get(device)) is not None and reg.platform == DOMAIN:
            errors[CONF_VOLUME_ENTITY] = "volume_is_room"  # a room player (loops)
        elif state is not None and state.attributes.get(ATTR_MASS_PLAYER_TYPE) == MASS_TYPE_GROUP:
            errors[CONF_VOLUME_ENTITY] = "group_player"
        elif (
            state is not None
            and device.startswith("media_player.")
            and not int(state.attributes.get(ATTR_SUPPORTED_FEATURES, 0) or 0) & MediaPlayerEntityFeature.VOLUME_SET
        ):
            errors[CONF_VOLUME_ENTITY] = "no_volume"

    roles = {k: list(options.get(k) or []) for k in (CONF_DISPLAY, CONF_SPEAKERS, CONF_BASS)}
    if roles[CONF_DISPLAY] and not video:
        errors[CONF_DISPLAY] = "display_needs_video"
    seen: set[str] = set()
    for key, entities in roles.items():
        if seen & set(entities):
            errors[key] = "duplicate_device"
        seen |= set(entities)
    for key in (CONF_VIDEO_HOST, CONF_AUDIO_HOST):
        if options.get(key) and options[key] in seen:
            errors[key] = "duplicate_device"
    if options.get(CONF_VIDEO_HOST) and not video:
        errors[CONF_VIDEO_HOST] = "needs_player"
    if options.get(CONF_AUDIO_HOST) and not audio:
        errors[CONF_AUDIO_HOST] = "needs_player"
    return errors


def _without_volume(hass: HomeAssistant, options: dict) -> list[str]:
    """Chosen players that cannot set a volume (room volume does not work for them)."""
    out: list[str] = []
    if options.get(CONF_VOLUME_CONTROL) == VOLUME_DEVICE:
        return out  # the device sets the room's volume
    for key in (CONF_VIDEO_PLAYER, CONF_AUDIO_PLAYER):
        state = hass.states.get(options[key]) if options.get(key) else None
        if state is None:
            continue  # not known right now: nothing to say
        features = int(state.attributes.get(ATTR_SUPPORTED_FEATURES, 0) or 0)
        if not features & MediaPlayerEntityFeature.VOLUME_SET:
            out.append(str(state.attributes.get(ATTR_FRIENDLY_NAME) or state.entity_id))
    return out


def _area_suggestions(hass: HomeAssistant, area_id: str | None) -> dict[str, str]:
    """Pre-select Kodi / Music Assistant players of the chosen area."""
    if not area_id:
        return {}
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    found: dict[str, str] = {}
    for entry in ent_reg.entities.values():
        if entry.domain != "media_player" or entry.disabled:
            continue
        entry_area = entry.area_id
        if entry_area is None and entry.device_id:
            device = dev_reg.async_get(entry.device_id)
            entry_area = device.area_id if device else None
        if entry_area != area_id:
            continue
        state = hass.states.get(entry.entity_id)
        if state is not None and state.attributes.get(ATTR_MASS_PLAYER_TYPE) == MASS_TYPE_GROUP:
            continue
        if entry.platform in VIDEO_PLATFORMS:
            found.setdefault(CONF_VIDEO_PLAYER, entry.entity_id)
        elif entry.platform in AUDIO_PLATFORMS:
            found.setdefault(CONF_AUDIO_PLAYER, entry.entity_id)
    return found


class BetterMediaPlayerConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 3

    def __init__(self) -> None:
        self._options: dict[str, Any] = {}
        self._title = ""

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["room", "hub"])

    async def async_step_room(self, user_input: dict | None = None) -> ConfigFlowResult:
        schema = schema_room(True)
        if user_input is not None:
            self._title = user_input.pop(CONF_NAME)
            self._options = _merge(self._options, "room", user_input)
            for key, value in _area_suggestions(self.hass, self._options.get(CONF_AREA)).items():
                self._options.setdefault(key, value)
            return await self.async_step_players()
        return self.async_show_form(step_id="room", data_schema=schema)

    async def async_step_hub(self, user_input: dict | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(ENTRY_TYPE_HUB)
        self._abort_if_unique_id_configured(error="hub_exists")
        for entry in self._async_current_entries():
            if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_HUB:
                return self.async_abort(reason="hub_exists")
        schema = schema_hub()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_hub(self.hass, user_input)
            if not errors:
                options = {k: v for k, v in user_input.items() if v not in (None, "", [])}
                return self.async_create_entry(
                    title="Better Media Player", data={CONF_ENTRY_TYPE: ENTRY_TYPE_HUB}, options=options
                )
        return self.async_show_form(
            step_id="hub", data_schema=self.add_suggested_values_to_schema(schema, user_input or {}), errors=errors
        )

    async def async_step_players(self, user_input: dict | None = None) -> ConfigFlowResult:
        return await self._step("players", user_input, self.async_step_devices)

    async def async_step_devices(self, user_input: dict | None = None) -> ConfigFlowResult:
        return await self._step("devices", user_input, self.async_step_volume_note)

    async def async_step_volume_note(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Only a note: a chosen player cannot set a volume."""
        players = _without_volume(self.hass, self._options)
        if user_input is not None or not players:
            return await self._finish()
        return self.async_show_form(
            step_id="volume_note", data_schema=vol.Schema({}), description_placeholders={"players": ", ".join(players)}
        )

    async def _step(self, step_id: str, user_input, next_step) -> ConfigFlowResult:
        schema = schema_step(step_id)
        errors: dict[str, str] = {}
        if user_input is not None:
            candidate = _merge(self._options, step_id, user_input)
            errors = _form_errors(step_id, _validate(self.hass, candidate))
            if not errors:
                self._options = candidate
                return await next_step()
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(schema, _nested(step_id, self._options)),
            errors=errors,
        )

    async def _finish(self, _user_input=None) -> ConfigFlowResult:
        options = {**DEFAULTS, **self._options}
        return self.async_create_entry(
            title=self._title, data={CONF_ENTRY_TYPE: ENTRY_TYPE_ROOM}, options=options
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        if config_entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_HUB:
            return HubOptionsFlow()
        return BetterMediaPlayerOptionsFlow()


class BetterMediaPlayerOptionsFlow(OptionsFlow):
    """Menu: each section on its own, then save."""

    def __init__(self) -> None:
        self._options: dict[str, Any] | None = None

    @property
    def options(self) -> dict[str, Any]:
        if self._options is None:
            self._options = {**DEFAULTS, **self.config_entry.options}
        return self._options

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=[*STEPS, "save"])

    async def async_step_save(self, user_input=None) -> ConfigFlowResult:
        errors = _validate(self.hass, self.options)
        if errors:
            return self.async_abort(reason="invalid_config")
        return self.async_create_entry(data=self.options)

    async def _section(self, step_id: str, user_input) -> ConfigFlowResult:
        schema = schema_step(step_id)
        errors: dict[str, str] = {}
        if user_input is not None:
            candidate = _merge(self.options, step_id, user_input)
            errors = _form_errors(step_id, _validate(self.hass, candidate))
            if not errors:
                self._options = candidate
                if step_id in ("players", "devices") and _without_volume(self.hass, self.options):
                    return await self.async_step_volume_note()
                return await self.async_step_init()
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(schema, _nested(step_id, self.options)),
            errors=errors,
        )

    async def async_step_volume_note(self, user_input=None) -> ConfigFlowResult:
        players = _without_volume(self.hass, self.options)
        if user_input is not None or not players:
            return await self.async_step_init()
        return self.async_show_form(
            step_id="volume_note", data_schema=vol.Schema({}), description_placeholders={"players": ", ".join(players)}
        )

    async def async_step_room(self, user_input=None) -> ConfigFlowResult:
        return await self._section("room", user_input)

    async def async_step_players(self, user_input=None) -> ConfigFlowResult:
        return await self._section("players", user_input)

    async def async_step_devices(self, user_input=None) -> ConfigFlowResult:
        return await self._section("devices", user_input)


class HubOptionsFlow(OptionsFlow):
    """Central settings: one form."""

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        schema = schema_hub()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_hub(self.hass, user_input)
            if not errors:
                return self.async_create_entry(
                    data={k: v for k, v in user_input.items() if v not in (None, "", [])}
                )
        current = dict(self.config_entry.options)
        return self.async_show_form(
            step_id="init", data_schema=self.add_suggested_values_to_schema(schema, current), errors=errors
        )

