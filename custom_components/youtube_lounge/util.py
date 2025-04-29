"""Utility for youtube lounge integration."""

from homeassistant.core import HomeAssistant


def device_name(hass: HomeAssistant) -> str:
    """Get device name to show on YouTube."""

    return f"Home Assistant {hass.config.location_name}"
