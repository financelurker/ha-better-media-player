"""Nothing of Home Assistant that is deprecated."""

from pathlib import Path

from custom_components.better_media_player import controller

SOURCE = Path(controller.__file__).parent


def test_no_deprecated_standby_state():
    """MediaPlayerState.STANDBY logs a deprecation warning at every start of Home Assistant."""
    users = [p.name for p in SOURCE.rglob("*.py") if "MediaPlayerState.STANDBY" in p.read_text(encoding="utf-8")]
    assert users == []


def test_standby_still_counts_as_stopped():
    """A player that still reports "standby" is stopped, as before."""
    assert "standby" in controller.VIDEO_STOPPED
    assert "standby" in controller.AUDIO_STOPPED
