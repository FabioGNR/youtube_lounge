"""Config flow for YouTube Lounge integration."""

from __future__ import annotations

import logging
from typing import Any
from dataclasses import dataclass

from aiogoogle import Aiogoogle
from aiogoogle.excs import HTTPError
from pyytlounge import YtLoungeApi
from pyytlounge.dial import get_screen_id_from_dial

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import ssdp
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

import aiohttp
from .const import DOMAIN
from .util import device_name

_LOGGER = logging.getLogger(__name__)

STEP_GOOGLE_API_DATA_SCHEMA = vol.Schema(
    {
        vol.Optional("google_api_key", description="google_api_key"): str,
    }
)

STEP_PAIR_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("pairing_code", description="pairing_code"): str,
    }
)


@dataclass
class ConnectResult:
    """Result obtained from pairing with a screen"""

    screen_name: str
    screen_id: str
    auth: dict


async def validate_google_api_key(api_key: str) -> dict[str, Any]:
    """Validate the user input allows us to connect."""

    # supplying a key is optional
    if api_key:
        async with Aiogoogle(api_key=api_key) as aiogoogle:
            yt_api = await aiogoogle.discover("youtube", "v3")
            request = yt_api.videos.list(part="snippet", id="oa__fLArsFk")
            try:
                await aiogoogle.as_api_key(request)
            except HTTPError as ex:
                if ex.res and ex.res.status_code == 400:
                    raise InvalidAuth from ex
                else:
                    raise CannotConnect from ex
            except Exception as ex:
                raise CannotConnect from ex

    return {}


async def validate_pairing_code(
    hass: HomeAssistant, pairing_code: str
) -> ConnectResult:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_PAIR_DATA_SCHEMA with values provided by the user.
    """

    async with YtLoungeApi(device_name(hass)) as api:
        try:
            if await api.pair(pairing_code):
                return ConnectResult(
                    screen_name=api.screen_name,
                    screen_id=api.auth.screen_id,
                    auth=api.auth.serialize(),
                )
        except aiohttp.ClientConnectionError as exc:
            raise CannotConnect from exc
        except aiohttp.ClientError as exc:
            raise InvalidAuth from exc
        except Exception as exc:
            raise InvalidAuth from exc


async def validate_screen_id(
    hass: HomeAssistant, screen_id: str, screen_name: str
) -> ConnectResult:
    """Validate the user input allows us to connect.

    Data has the key screen_id with values provided by automatic discovery.
    """

    async with YtLoungeApi(device_name(hass)) as api:
        try:
            if await api.pair_with_screen_id(screen_id, screen_name):
                return ConnectResult(
                    screen_name=api.screen_name,
                    screen_id=api.auth.screen_id,
                    auth=api.auth.serialize(),
                )

        except aiohttp.ClientConnectionError as exc:
            raise CannotConnect from exc
        except aiohttp.ClientError as exc:
            raise InvalidAuth from exc
        except:
            raise InvalidAuth


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for YouTube Lounge."""

    VERSION = 1

    connect_result: ConnectResult | None = None

    def __init__(self):
        self.connect_result = None

    async def async_step_ssdp(self, discovery_info: ssdp.SsdpServiceInfo) -> FlowResult:
        """Prepare configuration for a SSDP discovered dial device."""

        _LOGGER.debug(
            "Found DIAL device through SSDP, checking if YouTube is available..."
        )

        dial_result = await get_screen_id_from_dial(discovery_info.ssdp_location)
        if dial_result:
            _LOGGER.info(
                "Found DIAL device through SSDP, YouTube is available: %s",
                dial_result.screen_name,
            )
            return await self._connect_with_screen_id(
                dial_result.screen_id, dial_result.screen_name
            )

        return self.async_abort(reason="Could not find YouTube on DIAL device")

    async def async_step_confirm_discovery(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the step to confirm set up after discovery"""
        if user_input is None:
            return self.async_show_form(
                step_id="confirm_discovery",
                description_placeholders={"name": self.connect_result.screen_name},
            )

        return await self.async_step_google_api_key()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step to set up through pairing code."""

        return await self.async_step_pair(user_input)

    async def async_step_google_api_key(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the step to set the google api key."""
        if user_input is None or "google_api_key" not in user_input:
            return self.async_show_form(
                step_id="google_api_key",
                data_schema=STEP_GOOGLE_API_DATA_SCHEMA,
                last_step=True,
            )

        errors = {}
        try:
            if "google_api_key" in user_input:
                await validate_google_api_key(user_input["google_api_key"])
        except CannotConnect:
            errors["base"] = "cannot_connect"
        except InvalidAuth:
            errors["base"] = "invalid_auth"
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception")
            errors["base"] = "unknown"
        else:
            return self.async_create_entry(
                title=self.connect_result.screen_name,
                data={
                    "auth": self.connect_result.auth,
                    "google_api_key": user_input.get("google_api_key", None),
                },
            )

        return self.async_show_form(
            step_id="google_api_key",
            data_schema=STEP_GOOGLE_API_DATA_SCHEMA,
            errors=errors,
            last_step=True,
        )

    async def _connect_with_screen_id(
        self, screen_id: str, screen_name: str
    ) -> FlowResult:
        """Handle the pairing step through screen id."""
        if not screen_id:
            return self.async_abort(reason="Screen id missing")

        errors = {}
        await self.async_set_unique_id(screen_id)
        self._abort_if_unique_id_configured()

        try:
            self.connect_result = await validate_screen_id(
                self.hass, screen_id, screen_name
            )
        except CannotConnect:
            errors["base"] = "cannot_connect"
        except InvalidAuth:
            errors["base"] = "invalid_auth"
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception")
            errors["base"] = "unknown"
        else:
            return await self.async_step_confirm_discovery()

        # TODO: display errors
        return self.async_abort(reason="Screen id failed")

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the pairing step."""
        if user_input is None or "pairing_code" not in user_input:
            return self.async_show_form(data_schema=STEP_PAIR_DATA_SCHEMA)

        errors = {}

        try:
            self.connect_result = await validate_pairing_code(
                self.hass, user_input["pairing_code"]
            )
        except CannotConnect:
            errors["base"] = "cannot_connect"
        except InvalidAuth:
            errors["base"] = "invalid_auth"
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception")
            errors["base"] = "unknown"
        else:
            await self.async_set_unique_id(self.connect_result.screen_id)
            self._abort_if_unique_id_configured()
            return await self.async_step_google_api_key(user_input)

        return self.async_show_form(data_schema=STEP_PAIR_DATA_SCHEMA, errors=errors)


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
