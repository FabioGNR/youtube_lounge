"""The YouTube Lounge integration."""
from __future__ import annotations

from pyytlounge import YtLoungeApi

from homeassistant.config_entries import ConfigEntry, ConfigEntryAuthFailed
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN, LOGGER
from .util import device_name

PLATFORMS: list[Platform] = [Platform.MEDIA_PLAYER]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up YouTube Lounge from a config entry."""

    hass.data.setdefault(DOMAIN, {})

    api = YtLoungeApi(device_name(hass), logger=LOGGER)
    api.auth.deserialize(entry.data["auth"])

    if not api.paired():
        raise ConfigEntryAuthFailed("Not paired")

    async def create_entry():
        hass.data[DOMAIN][entry.entry_id] = api
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if await api.connect():
        await create_entry()
        return True

    # try refresh auth first
    if await api.refresh_auth():
        if await api.connect():
            await create_entry()
            return True

    return False


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        api = hass.data[DOMAIN].pop(entry.entry_id)
        api.close()

    return unload_ok
