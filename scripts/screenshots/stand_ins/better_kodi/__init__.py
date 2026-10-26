"""Stand-in for Better Kodi in the screenshot instance (not the real integration).

Kodi players from YAML (configuration.yaml), so the rooms get a video player with handover; the
handover itself only moves the stand-in's state.
"""

from homeassistant.core import HomeAssistant, ServiceCall

DOMAIN = "better_kodi"
PLAYERS: dict = {}


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    async def handover(call: ServiceCall) -> None:
        for source in call.data["entity_id"] if isinstance(call.data["entity_id"], list) else [call.data["entity_id"]]:
            src, dst = PLAYERS.get(source), PLAYERS.get(call.data["destination"])
            if src and dst:
                dst.take_over(src)
                if not call.data.get("keep_source_playing"):
                    await src.async_turn_off()

    hass.services.async_register(DOMAIN, "handover", handover)
    return True
