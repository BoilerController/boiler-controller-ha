"""Client for interacting with a Shelly dimmer via RPC endpoints."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    SHELLY_RPC_DEVICE_INFO,
    SHELLY_RPC_LIGHT_CONFIG,
    SHELLY_RPC_LIGHT_SET,
    SHELLY_RPC_LIGHT_STATUS,
)

_LOGGER = logging.getLogger(__name__)


def normalize_device_id(device_id: str | None) -> str | None:
    """Normalize Shelly device identifiers for comparisons."""
    if not device_id:
        return None
    return str(device_id).strip().lower()


class ShellyClient:
    """Helper class to interact with Shelly RPC API."""

    def __init__(self, hass: HomeAssistant, base_url: str, light_id: int = 0) -> None:
        self.hass = hass
        self.base_url = base_url.rstrip("/")
        self._light_id = light_id
        self._session = async_get_clientsession(hass)

    @staticmethod
    def extract_device_id(payload: Dict[str, Any] | None) -> str | None:
        """Extract and normalize the Shelly device identifier from RPC payloads."""
        if not payload:
            return None

        candidate = (
            payload.get("id")
            or payload.get("device_id")
            or payload.get("mac")
            or payload.get("name")
        )
        return normalize_device_id(candidate)

    def _channel_params(self) -> Dict[str, int]:
        """Return RPC params targeting the configured light channel."""
        channel_id = 0 if self._light_id is None else int(self._light_id)
        return {"id": channel_id}

    async def async_get_status(self) -> Optional[Dict[str, Any]]:
        """Fetch current status from the Shelly device."""
        url = f"{self.base_url}{SHELLY_RPC_LIGHT_STATUS}"
        try:
            async with self._session.get(
                url,
                params=self._channel_params(),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    _LOGGER.debug("Shelly status: %s", data)
                    return data
                _LOGGER.warning("Shelly status request failed with %s", response.status)
        except aiohttp.ClientError as err:
            _LOGGER.warning("Shelly status request error: %s", err)
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("Unexpected Shelly status error: %s", err)
        return None

    async def _async_light_set(self, payload: Dict[str, Any]) -> bool:
        """Call the Shelly Light.Set RPC method via GET with query params."""
        url = f"{self.base_url}{SHELLY_RPC_LIGHT_SET}"
        request_params = dict(self._channel_params())
        request_params.update(payload)

        safe_params: Dict[str, Any] = {}
        for key, value in request_params.items():
            safe_params[key] = int(value) if isinstance(value, bool) else value

        if _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug("Shelly Light.Set params=%s", safe_params)

        try:
            async with self._session.get(
                url,
                params=safe_params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status == 200:
                    return True
                body = await response.text()
                _LOGGER.warning(
                    "Shelly Light.Set failed with %s: %s",
                    response.status,
                    body.strip() or "<empty>",
                )
        except aiohttp.ClientError as err:
            _LOGGER.warning("Shelly Light.Set error: %s", err)
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("Unexpected Shelly Light.Set error: %s", err)
        return False

    async def async_set_brightness(self, brightness: int) -> bool:
        """Set dimmer brightness (0-100)."""
        clamped = max(0, min(100, int(brightness)))
        payload = {
            "brightness": clamped,
            "on": bool(clamped),
        }
        return await self._async_light_set(payload)

    async def async_turn_off(self) -> bool:
        """Turn off the Shelly dimmer."""
        return await self._async_light_set({"on": False})

    async def async_test_connection(self) -> bool:
        """Check whether the Shelly is reachable."""
        status = await self.async_get_status()
        return status is not None

    async def async_get_light_config(self) -> Optional[Dict[str, Any]]:
        """Fetch static configuration for the Shelly light channel."""
        url = f"{self.base_url}{SHELLY_RPC_LIGHT_CONFIG}"
        try:
            async with self._session.get(
                url,
                params=self._channel_params(),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    _LOGGER.debug("Shelly light config: %s", data)
                    return data
                body = await response.text()
                _LOGGER.debug(
                    "Shelly light config request failed with %s: %s",
                    response.status,
                    body.strip() or "<empty>",
                )
        except aiohttp.ClientError as err:
            _LOGGER.debug("Shelly light config error: %s", err)
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("Unexpected Shelly light config error: %s", err)
        return None

    async def async_get_device_info(self) -> Optional[Dict[str, Any]]:
        """Fetch general Shelly device information."""
        url = f"{self.base_url}{SHELLY_RPC_DEVICE_INFO}"
        try:
            async with self._session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    _LOGGER.debug("Shelly device info: %s", data)
                    return data
                body = await response.text()
                _LOGGER.debug(
                    "Shelly device info request failed with %s: %s",
                    response.status,
                    body.strip() or "<empty>",
                )
        except aiohttp.ClientError as err:
            _LOGGER.debug("Shelly device info error: %s", err)
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("Unexpected Shelly device info error: %s", err)
        return None
