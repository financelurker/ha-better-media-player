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

from .const import (
    ACTION_DOMAINS,
    ATTR_MASS_PLAYER_TYPE,
    CONF_AREA,
    CONF_AUDIO_HOST,
    CONF_AUDIO_OFF_ACTION,
    CONF_AUDIO_ON_ACTION,
    CONF_AUDIO_ON_WAIT,
    CONF_AUDIO_PLAYER,
    CONF_AUDIO_POWER,
    CONF_AUDIO_RUN_DOWN,
    CONF_AUDIO_STANDBY,
    CONF_AUDIO_STANDBY_MAX,
    CONF_AUDIO_STANDBY_W,
    CONF_BASS,
    CONF_DEFAULT_VOLUME,
    CONF_DISPLAY,
    CONF_DISPLAY_ON_WAIT,
    CONF_DISPLAY_POWER,
    CONF_DISPLAY_RUN_DOWN,
    CONF_DISPLAY_STANDBY,
    CONF_DISPLAY_STANDBY_MAX,
    CONF_DISPLAY_STANDBY_W,
    CONF_ENTRY_TYPE,
    CONF_FOLLOW_PLAYER,
    CONF_HOST_ON_WAIT,
    CONF_HOST_RUN_DOWN,
    CONF_OCCUPANCY,
    CONF_PRESENCE_RUN_DOWN,
    CONF_PREWARM,
    CONF_WAKE_DETECT_LEFT,
    CONF_NIGHT_DISPLAY_RUN_DOWN,
    CONF_NIGHT_FADE_IN,
    CONF_NIGHT_MAX_VOLUME,
    CONF_NIGHT_VIDEO_IDLE_TIMEOUT,
    CONF_SLEEP_FADE,
    CONF_SNOOZE_MINUTES,
    CONF_SPEAKERS,
    CONF_START_TIMEOUT,
    CONF_VIDEO_HOST,
    CONF_VIDEO_IDLE_TIMEOUT,
    CONF_VIDEO_OFF_ACTION,
    CONF_VIDEO_ON_ACTION,
    CONF_VIDEO_PLAYER,
    CONF_VOLUME_OFFSET,
    DEFAULTS,
    DOMAIN,
    ENTRY_TYPE_HUB,
    ENTRY_TYPE_ROOM,
    HUB_DEFAULTS,
    MASS_TYPE_GROUP,
    SWITCH_DOMAINS,
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


def schema_sources() -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(CONF_VIDEO_PLAYER): _entity("media_player"),
            vol.Optional(CONF_AUDIO_PLAYER): _entity("media_player"),
        }
    )


def schema_devices() -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(CONF_DISPLAY): _entity(SWITCH_DOMAINS, multiple=True),
            vol.Optional(CONF_SPEAKERS): _entity(SWITCH_DOMAINS, multiple=True),
            vol.Optional(CONF_BASS): _entity(SWITCH_DOMAINS, multiple=True),
        }
    )


def schema_advanced() -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(CONF_VIDEO_ON_ACTION): _entity(ACTION_DOMAINS),
            vol.Optional(CONF_VIDEO_OFF_ACTION): _entity(ACTION_DOMAINS),
            vol.Optional(CONF_VIDEO_HOST): _entity(SWITCH_DOMAINS),
            vol.Optional(CONF_AUDIO_ON_ACTION): _entity(ACTION_DOMAINS),
            vol.Optional(CONF_AUDIO_OFF_ACTION): _entity(ACTION_DOMAINS),
            vol.Optional(CONF_AUDIO_HOST): _entity(SWITCH_DOMAINS),
        }
    )


def schema_night() -> vol.Schema:
    d = DEFAULTS
    return vol.Schema(
        {
            vol.Optional(CONF_NIGHT_MAX_VOLUME, default=d[CONF_NIGHT_MAX_VOLUME]): _num(0, 100, 1, "%"),
            vol.Optional(CONF_NIGHT_VIDEO_IDLE_TIMEOUT, default=d[CONF_NIGHT_VIDEO_IDLE_TIMEOUT]): _num(0, 480, 1, "min"),
            vol.Optional(CONF_NIGHT_DISPLAY_RUN_DOWN, default=d[CONF_NIGHT_DISPLAY_RUN_DOWN]): _num(0, 120, 1, "min"),
            vol.Optional(CONF_NIGHT_FADE_IN, default=d[CONF_NIGHT_FADE_IN]): _num(0, 30, 1, "min"),
        }
    )


def schema_timing() -> vol.Schema:
    d = DEFAULTS
    return vol.Schema(
        {
            vol.Optional(CONF_DISPLAY_RUN_DOWN, default=d[CONF_DISPLAY_RUN_DOWN]): _num(0, 120, 1, "min"),
            vol.Optional(CONF_AUDIO_RUN_DOWN, default=d[CONF_AUDIO_RUN_DOWN]): _num(0, 120, 1, "min"),
            vol.Optional(CONF_HOST_RUN_DOWN, default=d[CONF_HOST_RUN_DOWN]): _num(0, 120, 1, "min"),
            vol.Optional(CONF_VIDEO_IDLE_TIMEOUT, default=d[CONF_VIDEO_IDLE_TIMEOUT]): _num(0, 480, 5, "min"),
            vol.Optional(CONF_START_TIMEOUT, default=d[CONF_START_TIMEOUT]): _num(10, 600, 5, "s"),
            vol.Optional(CONF_HOST_ON_WAIT, default=d[CONF_HOST_ON_WAIT]): _num(0, 120, 1, "s"),
            vol.Optional(CONF_DISPLAY_ON_WAIT, default=d[CONF_DISPLAY_ON_WAIT]): _num(0, 120, 1, "s"),
            vol.Optional(CONF_AUDIO_ON_WAIT, default=d[CONF_AUDIO_ON_WAIT]): _num(0, 120, 1, "s"),
            vol.Optional(CONF_SLEEP_FADE, default=d[CONF_SLEEP_FADE]): _num(0, 60, 1, "min"),
            vol.Optional(CONF_SNOOZE_MINUTES, default=d[CONF_SNOOZE_MINUTES]): _num(1, 120, 1, "min"),
        }
    )


def schema_standby() -> vol.Schema:
    d = DEFAULTS
    return vol.Schema(
        {
            vol.Optional(CONF_DISPLAY_POWER): _entity("sensor", "power", multiple=True),
            vol.Optional(CONF_DISPLAY_STANDBY, default=d[CONF_DISPLAY_STANDBY]): sel.BooleanSelector(),
            vol.Optional(CONF_DISPLAY_STANDBY_W, default=d[CONF_DISPLAY_STANDBY_W]): _num(0, 200, 0.5, "W"),
            vol.Optional(CONF_DISPLAY_STANDBY_MAX, default=d[CONF_DISPLAY_STANDBY_MAX]): _num(1, 120, 1, "min"),
            vol.Optional(CONF_AUDIO_POWER): _entity("sensor", "power", multiple=True),
            vol.Optional(CONF_AUDIO_STANDBY, default=d[CONF_AUDIO_STANDBY]): sel.BooleanSelector(),
            vol.Optional(CONF_AUDIO_STANDBY_W, default=d[CONF_AUDIO_STANDBY_W]): _num(0, 200, 0.5, "W"),
            vol.Optional(CONF_AUDIO_STANDBY_MAX, default=d[CONF_AUDIO_STANDBY_MAX]): _num(1, 120, 1, "min"),
        }
    )


def schema_follow() -> vol.Schema:
    d = DEFAULTS
    return vol.Schema(
        {
            vol.Optional(CONF_OCCUPANCY): _entity("binary_sensor", multiple=True),
            vol.Optional(CONF_PRESENCE_RUN_DOWN, default=d[CONF_PRESENCE_RUN_DOWN]): _num(0, 60, 0.5, "min"),
            vol.Optional(CONF_VOLUME_OFFSET, default=d[CONF_VOLUME_OFFSET]): _num(-50, 50, 1, "%"),
            vol.Optional(CONF_PREWARM, default=d[CONF_PREWARM]): sel.BooleanSelector(),
            vol.Optional(CONF_WAKE_DETECT_LEFT, default=d[CONF_WAKE_DETECT_LEFT]): sel.BooleanSelector(),
        }
    )


def schema_hub() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_FOLLOW_PLAYER): _entity("media_player"),
            vol.Optional(
                CONF_DEFAULT_VOLUME, default=HUB_DEFAULTS[CONF_DEFAULT_VOLUME]
            ): _num(1, 100, 1, "%"),
        }
    )


def _validate_hub(hass: HomeAssistant, options: dict) -> dict[str, str]:
    state = hass.states.get(options.get(CONF_FOLLOW_PLAYER) or "")
    if state is not None and state.attributes.get(ATTR_MASS_PLAYER_TYPE) != MASS_TYPE_GROUP:
        return {CONF_FOLLOW_PLAYER: "not_a_group"}
    return {}


STEPS = {
    "room": lambda: schema_room(False),
    "sources": schema_sources,
    "devices": schema_devices,
    "advanced": schema_advanced,
    "timing": schema_timing,
    "standby": schema_standby,
    "night": schema_night,
    "follow": schema_follow,
}


def _keys(schema: vol.Schema) -> list[str]:
    return [str(k.schema) for k in schema.schema]


def _merge(options: dict, schema: vol.Schema, user_input: dict) -> dict:
    """Take a step's input; drop optional fields that were cleared."""
    out = {k: v for k, v in options.items() if k not in _keys(schema)}
    out.update({k: v for k, v in user_input.items() if v not in (None, "", [])})
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
    if options.get(CONF_DISPLAY_STANDBY) and not options.get(CONF_DISPLAY_POWER):
        errors[CONF_DISPLAY_STANDBY] = "needs_power"
    if options.get(CONF_AUDIO_STANDBY) and not options.get(CONF_AUDIO_POWER):
        errors[CONF_AUDIO_STANDBY] = "needs_power"
    return errors


def _without_volume(hass: HomeAssistant, options: dict) -> list[str]:
    """Chosen players that cannot set a volume (room volume does not work for them)."""
    out = []
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
    VERSION = 2

    def __init__(self) -> None:
        self._options: dict[str, Any] = {}
        self._title = ""

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["room", "hub"])

    async def async_step_room(self, user_input: dict | None = None) -> ConfigFlowResult:
        schema = schema_room(True)
        if user_input is not None:
            self._title = user_input.pop(CONF_NAME)
            self._options = _merge(self._options, schema, user_input)
            for key, value in _area_suggestions(self.hass, self._options.get(CONF_AREA)).items():
                self._options.setdefault(key, value)
            return await self.async_step_sources()
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
                options = {**HUB_DEFAULTS, **{k: v for k, v in user_input.items() if v not in (None, "", [])}}
                return self.async_create_entry(
                    title="Better Media Player", data={CONF_ENTRY_TYPE: ENTRY_TYPE_HUB}, options=options
                )
        return self.async_show_form(
            step_id="hub", data_schema=self.add_suggested_values_to_schema(schema, user_input or {}), errors=errors
        )

    async def async_step_sources(self, user_input: dict | None = None) -> ConfigFlowResult:
        return await self._step("sources", user_input, self.async_step_volume_note)

    async def async_step_volume_note(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Only a note: a chosen player cannot set a volume."""
        players = _without_volume(self.hass, self._options)
        if user_input is not None or not players:
            return await self.async_step_devices()
        return self.async_show_form(
            step_id="volume_note", data_schema=vol.Schema({}), description_placeholders={"players": ", ".join(players)}
        )

    async def async_step_devices(self, user_input: dict | None = None) -> ConfigFlowResult:
        return await self._step("devices", user_input, self.async_step_advanced)

    async def async_step_advanced(self, user_input: dict | None = None) -> ConfigFlowResult:
        return await self._step("advanced", user_input, self.async_step_timing)

    async def async_step_timing(self, user_input: dict | None = None) -> ConfigFlowResult:
        return await self._step("timing", user_input, self.async_step_standby)

    async def async_step_standby(self, user_input: dict | None = None) -> ConfigFlowResult:
        return await self._step("standby", user_input, self.async_step_night)

    async def async_step_night(self, user_input: dict | None = None) -> ConfigFlowResult:
        return await self._step("night", user_input, self.async_step_follow)

    async def async_step_follow(self, user_input: dict | None = None) -> ConfigFlowResult:
        return await self._step("follow", user_input, self._finish)

    async def _step(self, step_id: str, user_input, next_step) -> ConfigFlowResult:
        schema = STEPS[step_id]()
        errors: dict[str, str] = {}
        if user_input is not None:
            candidate = _merge(self._options, schema, user_input)
            errors = {k: v for k, v in _validate(self.hass, candidate).items() if k in _keys(schema)}
            if not errors:
                self._options = candidate
                return await next_step()
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(schema, self._options),
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
        schema = STEPS[step_id]()
        errors: dict[str, str] = {}
        if user_input is not None:
            candidate = _merge(self.options, schema, user_input)
            errors = {k: v for k, v in _validate(self.hass, candidate).items() if k in _keys(schema)}
            if not errors:
                self._options = candidate
                return await self.async_step_init()
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(schema, self.options),
            errors=errors,
        )

    async def async_step_room(self, user_input=None) -> ConfigFlowResult:
        return await self._section("room", user_input)

    async def async_step_sources(self, user_input=None) -> ConfigFlowResult:
        result = await self._section("sources", user_input)
        if user_input is not None and result.get("step_id") == "init" and _without_volume(self.hass, self.options):
            return await self.async_step_volume_note()
        return result

    async def async_step_volume_note(self, user_input=None) -> ConfigFlowResult:
        players = _without_volume(self.hass, self.options)
        if user_input is not None or not players:
            return await self.async_step_init()
        return self.async_show_form(
            step_id="volume_note", data_schema=vol.Schema({}), description_placeholders={"players": ", ".join(players)}
        )

    async def async_step_devices(self, user_input=None) -> ConfigFlowResult:
        return await self._section("devices", user_input)

    async def async_step_advanced(self, user_input=None) -> ConfigFlowResult:
        return await self._section("advanced", user_input)

    async def async_step_timing(self, user_input=None) -> ConfigFlowResult:
        return await self._section("timing", user_input)

    async def async_step_standby(self, user_input=None) -> ConfigFlowResult:
        return await self._section("standby", user_input)

    async def async_step_night(self, user_input=None) -> ConfigFlowResult:
        return await self._section("night", user_input)

    async def async_step_follow(self, user_input=None) -> ConfigFlowResult:
        return await self._section("follow", user_input)


class HubOptionsFlow(OptionsFlow):
    """Central settings: one form."""

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        schema = schema_hub()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_hub(self.hass, user_input)
            if not errors:
                return self.async_create_entry(
                    data={**HUB_DEFAULTS, **{k: v for k, v in user_input.items() if v not in (None, "", [])}}
                )
        current = {**HUB_DEFAULTS, **self.config_entry.options}
        return self.async_show_form(
            step_id="init", data_schema=self.add_suggested_values_to_schema(schema, current), errors=errors
        )

