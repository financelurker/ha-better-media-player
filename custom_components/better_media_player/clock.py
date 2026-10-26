"""The time the integration counts with (seconds since the epoch, as a float)."""

from homeassistant.util import dt as dt_util


def now() -> float:
    return dt_util.utcnow().timestamp()
