"""Constants for Better Media Player."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "better_media_player"
NAME = "Better Media Player"

PLATFORMS: list[Platform] = [
    Platform.MEDIA_PLAYER,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.BUTTON,
    Platform.SELECT,
]

STORAGE_VERSION = 1
TICK_SECONDS = 10

# Entry types: one per room, and one central entry (follow me).
CONF_ENTRY_TYPE = "entry_type"
ENTRY_TYPE_ROOM = "room"
ENTRY_TYPE_HUB = "hub"

HUB_PLATFORMS: list[Platform] = [Platform.SENSOR]

# --- Hub (central settings) ----------------------------------------------------
CONF_FOLLOW_PLAYER = "follow_player"  # the follow-me player: an MA group with dynamic members
CONF_DEFAULT_VOLUME = "default_volume"  # %

# controls that make no sense right now are hidden (all rooms), after a delay (s)
CONF_DYNAMIC_CONTROLS = "dynamic_controls"
CONF_HIDE_DELAY = "hide_delay"
HUB_DEFAULTS: dict = {CONF_DEFAULT_VOLUME: 25, CONF_DYNAMIC_CONTROLS: True, CONF_HIDE_DELAY: 3}

SERVICE_FOLLOW_START = "start_follow"
SERVICE_FOLLOW_STOP = "stop_follow"

# --- Room: follow me ----------------------------------------------------------
CONF_OCCUPANCY = "occupancy"
CONF_PRESENCE_RUN_DOWN = "presence_run_down"  # min
CONF_VOLUME_OFFSET = "volume_offset"  # percentage points
# where the room's volume is set: on the players, or on an own device
CONF_VOLUME_CONTROL = "volume_control"
CONF_VOLUME_ENTITY = "volume_entity"
VOLUME_PLAYERS = "players"
VOLUME_DEVICE = "device"
VOLUME_ENTITY_DOMAINS = ["media_player", "number", "input_number"]
# volume presets: fixed names, a volume (%) per room (number entities); 0: not offered
PRESETS = ["quiet", "normal", "loud", "party"]
PRESET_MANUAL = "manual"  # the volume was changed after the preset
PRESET_NONE = "none"
CONF_PRESET = "preset_{}"  # .format(name)
CONF_POWER_ON_PRESET = "power_on_preset"
PRESET_DEFAULTS = {"quiet": 15, "normal": 30, "loud": 50, "party": 70}
SERVICE_SET_VOLUME_PRESET = "set_volume_preset"
ATTR_PRESET = "preset"
CONF_PREWARM = "prewarm"
CONF_WAKE_DETECT_LEFT = "wake_detect_left"  # follow wake: leaving the wake room ends the wake
WAKE_ARRIVAL_WINDOW = 120  # s: an arrival elsewhere may come this much before the wake room went empty

FOLLOW_INACTIVE = "inactive"  # no follow group playing
FOLLOW_ACTIVE = "following"  # member of the playing follow group
FOLLOW_WAITING = "waiting"  # prewarmed, waiting for presence
FOLLOW_OVERRIDE = "override"  # member without presence (start or manual join while nobody is detected)
FOLLOW_EXCLUDED_QUIET = "excluded_quiet"
FOLLOW_EXCLUDED_VIDEO = "excluded_video"
FOLLOW_EXCLUDED_OFF = "excluded_off"  # room was turned off during this session
FOLLOW_EXCLUDED_BUSY = "excluded_busy"  # plays directly or in another group
FOLLOW_UNKNOWN = "presence_unknown"
FOLLOW_WAKING = "waking"  # wake room of a follow wake (held until it is left)
FOLLOW_SNOOZED = "snoozed"  # wake room, snoozed: out of the group meanwhile
FOLLOW_HELD_OUT = "held_out"  # occupied when a follow wake started: joins after its presence went and came
FOLLOW_STATES = [
    FOLLOW_INACTIVE,
    FOLLOW_ACTIVE,
    FOLLOW_WAITING,
    FOLLOW_OVERRIDE,
    FOLLOW_EXCLUDED_QUIET,
    FOLLOW_EXCLUDED_VIDEO,
    FOLLOW_EXCLUDED_OFF,
    FOLLOW_EXCLUDED_BUSY,
    FOLLOW_UNKNOWN,
    FOLLOW_WAKING,
    FOLLOW_SNOOZED,
    FOLLOW_HELD_OUT,
]

# --- Room --------------------------------------------------------------------
CONF_AREA = "area"

# --- Sources -----------------------------------------------------------------
SOURCE_VIDEO = "video"  # needs display + audio (e.g. Kodi)
SOURCE_AUDIO = "audio"  # needs audio only (e.g. Music Assistant / sendspin)
SOURCES = (SOURCE_VIDEO, SOURCE_AUDIO)

CONF_VIDEO_PLAYER = "video_player"
CONF_VIDEO_ON_ACTION = "video_on_action"
CONF_VIDEO_OFF_ACTION = "video_off_action"
CONF_VIDEO_HOST = "video_host"
CONF_AUDIO_PLAYER = "audio_player"
CONF_AUDIO_ON_ACTION = "audio_on_action"
CONF_AUDIO_OFF_ACTION = "audio_off_action"
CONF_AUDIO_HOST = "audio_host"

SOURCE_CONF = {
    SOURCE_VIDEO: (CONF_VIDEO_PLAYER, CONF_VIDEO_ON_ACTION, CONF_VIDEO_OFF_ACTION, CONF_VIDEO_HOST),
    SOURCE_AUDIO: (CONF_AUDIO_PLAYER, CONF_AUDIO_ON_ACTION, CONF_AUDIO_OFF_ACTION, CONF_AUDIO_HOST),
}

# --- Power roles -------------------------------------------------------------
ROLE_DISPLAY = "display"
ROLE_AUDIO = "audio"
ROLE_BASS = "bass"
ROLE_VIDEO_HOST = "video_host"
ROLE_AUDIO_HOST = "audio_host"

CONF_DISPLAY = "display"
CONF_SPEAKERS = "speakers"
CONF_BASS = "bass"

# --- Timing ------------------------------------------------------------------
CONF_DISPLAY_RUN_DOWN = "display_run_down"  # min
CONF_AUDIO_RUN_DOWN = "audio_run_down"  # min
CONF_HOST_RUN_DOWN = "host_run_down"  # min
CONF_VIDEO_IDLE_TIMEOUT = "video_idle_timeout"  # min, 0 = never
CONF_START_TIMEOUT = "start_timeout"  # s
CONF_DISPLAY_ON_WAIT = "display_on_wait"  # s
CONF_HOST_ON_WAIT = "host_on_wait"  # s
CONF_AUDIO_ON_WAIT = "audio_on_wait"  # s
CONF_SLEEP_FADE = "sleep_fade"  # min, default fade of the sleep timer

# --- Night mode ------------------------------------------------------------------
CONF_NIGHT_VIDEO_IDLE_TIMEOUT = "night_video_idle_timeout"  # min
CONF_NIGHT_DISPLAY_RUN_DOWN = "night_display_run_down"  # min
CONF_NIGHT_MAX_VOLUME = "night_max_volume"  # %, 0 = no limit
CONF_NIGHT_FADE_IN = "night_fade_in"  # min, back to the room volume afterwards
ATTR_NIGHT_MODE = "night_mode"

# --- Wake up ---------------------------------------------------------------------
CONF_SNOOZE_MINUTES = "snooze_minutes"
SERVICE_WAKE = "wake"
SERVICE_SNOOZE = "snooze"
SERVICE_STOP_WAKE = "stop_wake"
EVENT_WAKE_STARTED = f"{DOMAIN}_wake_started"
EVENT_WAKE_FAILED = f"{DOMAIN}_wake_failed"
EVENT_WAKE_FINISHED = f"{DOMAIN}_wake_finished"

# --- Standby (optional, needs power sensors) ----------------------------------
CONF_DISPLAY_POWER = "display_power"
CONF_DISPLAY_STANDBY = "display_standby"
CONF_DISPLAY_STANDBY_W = "display_standby_w"
CONF_DISPLAY_STANDBY_MAX = "display_standby_max"  # min
CONF_AUDIO_POWER = "audio_power"
CONF_AUDIO_STANDBY = "audio_standby"
CONF_AUDIO_STANDBY_W = "audio_standby_w"
CONF_AUDIO_STANDBY_MAX = "audio_standby_max"  # min
STANDBY_HOLD_SECONDS = 30

DEFAULTS: dict = {
    CONF_DISPLAY_RUN_DOWN: 10,
    CONF_AUDIO_RUN_DOWN: 2,
    CONF_HOST_RUN_DOWN: 2,
    CONF_VIDEO_IDLE_TIMEOUT: 30,
    CONF_START_TIMEOUT: 90,
    CONF_DISPLAY_ON_WAIT: 0,
    CONF_HOST_ON_WAIT: 0,
    CONF_AUDIO_ON_WAIT: 0,
    CONF_DISPLAY_STANDBY: False,
    CONF_DISPLAY_STANDBY_W: 5.0,
    CONF_DISPLAY_STANDBY_MAX: 20,
    CONF_AUDIO_STANDBY: False,
    CONF_AUDIO_STANDBY_W: 3.0,
    CONF_AUDIO_STANDBY_MAX: 5,
    CONF_PRESENCE_RUN_DOWN: 2,
    CONF_VOLUME_OFFSET: 0,
    CONF_VOLUME_CONTROL: VOLUME_PLAYERS,
    **{CONF_PRESET.format(name): value for name, value in PRESET_DEFAULTS.items()},
    CONF_POWER_ON_PRESET: PRESET_NONE,
    CONF_PREWARM: True,
    CONF_WAKE_DETECT_LEFT: True,
    CONF_SLEEP_FADE: 5,
    CONF_NIGHT_VIDEO_IDLE_TIMEOUT: 5,
    CONF_NIGHT_DISPLAY_RUN_DOWN: 1,
    CONF_NIGHT_MAX_VOLUME: 30,
    CONF_NIGHT_FADE_IN: 2,
    CONF_SNOOZE_MINUTES: 9,
}

# --- Sleep timer ---------------------------------------------------------------
SERVICE_SET_SLEEP_TIMER = "set_sleep_timer"
SERVICE_CANCEL_SLEEP_TIMER = "cancel_sleep_timer"
ATTR_MINUTES = "minutes"
ATTR_FADE_MINUTES = "fade_minutes"
EVENT_SLEEP_TIMER_FINISHED = f"{DOMAIN}_sleep_timer_finished"
EVENT_SOURCE_CHANGED = f"{DOMAIN}_source_changed"

# --- Status ------------------------------------------------------------------
STATUS_OFF = "off"
STATUS_STARTING = "starting"
STATUS_ON = "on"
STATUS_PLAYING = "playing"
STATUS_RUN_DOWN = "run_down"
STATUS_STOPPING = "stopping"
STATUS_MANUAL = "manual"
STATUS_ERROR = "error"
STATUS_DISABLED = "disabled"
STATUSES = [
    STATUS_OFF,
    STATUS_STARTING,
    STATUS_ON,
    STATUS_PLAYING,
    STATUS_RUN_DOWN,
    STATUS_STOPPING,
    STATUS_MANUAL,
    STATUS_ERROR,
    STATUS_DISABLED,
]

# Domains that can be switched as power devices
SWITCH_DOMAINS = ["switch", "light", "input_boolean"]
# Domains usable as a custom on/off action
ACTION_DOMAINS = ["script", "button", "input_button", "switch", "scene", "automation"]

# Music Assistant attribute names
ATTR_MASS_PLAYER_TYPE = "mass_player_type"
MASS_TYPE_GROUP = "group"
