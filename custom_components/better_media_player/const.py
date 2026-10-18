"""Constants for Better Media Player."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "better_media_player"
NAME = "Better Media Player"
VERSION = "0.2.0"

PLATFORMS: list[Platform] = [
    Platform.MEDIA_PLAYER,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.BUTTON,
]

STORAGE_VERSION = 1
TICK_SECONDS = 10

# Entry types. v0.1 only knows rooms; v0.2 adds a central "hub" entry in the
# same domain, so existing rooms never need to be migrated.
CONF_ENTRY_TYPE = "entry_type"
ENTRY_TYPE_ROOM = "room"
ENTRY_TYPE_HUB = "hub"

HUB_PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BUTTON]

# --- Hub (central settings) ----------------------------------------------------
CONF_FOLLOW_GROUPS = "follow_groups"
CONF_ALWAYS_GROUPS = "always_groups"
CONF_DEFAULT_VOLUME = "default_volume"  # %

POLICY_ALWAYS = "always"
POLICY_FOLLOW = "follow"
POLICY_IGNORE = "ignore"

HUB_DEFAULTS: dict = {CONF_DEFAULT_VOLUME: 25}

SERVICE_START_FOLLOW = "start_follow"
ATTR_GROUP = "group"
ATTR_VOLUME = "volume"
ATTR_FALLBACK_ROOMS = "fallback_rooms"

# --- Room: follow me ----------------------------------------------------------
CONF_OCCUPANCY = "occupancy"
CONF_PRESENCE_RUN_DOWN = "presence_run_down"  # min
CONF_VOLUME_OFFSET = "volume_offset"  # percentage points
CONF_PREWARM = "prewarm"

FOLLOW_INACTIVE = "inactive"  # no follow group playing
FOLLOW_ACTIVE = "following"  # member of the playing follow group
FOLLOW_WAITING = "waiting"  # prewarmed, waiting for presence
FOLLOW_OVERRIDE = "override"  # member without presence (manual / fallback start)
FOLLOW_EXCLUDED_QUIET = "excluded_quiet"
FOLLOW_EXCLUDED_VIDEO = "excluded_video"
FOLLOW_EXCLUDED_OFF = "excluded_off"  # room was turned off during this session
FOLLOW_EXCLUDED_BUSY = "excluded_busy"  # plays directly or in another group
FOLLOW_UNKNOWN = "presence_unknown"
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
    CONF_PREWARM: True,
}

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
