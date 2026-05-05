"""Client for interacting with the Boiler Controller module via HTTP API."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)


class BoilerClient:
    """HTTP API client for the Boiler Controller module."""

    def __init__(self, hass: HomeAssistant, host: str) -> None:
        self.hass = hass
        # host can be IP or mDNS hostname, e.g. "192.168.1.100"
        # or "boiler-controller-abcd1234.local"
        self._host = host.strip().rstrip("/")
        self._base_url = f"http://{self._host}"
        self._session = async_get_clientsession(hass)

    @property
    def host(self) -> str:
        return self._host

    async def async_get_status(self) -> Optional[Dict[str, Any]]:
        """Fetch /api/status from the module.

        Returns a dict like:
        {
            "power": 1320,
            "heatingPercentage": 60,
            "temperature": 65.0,
            "total": 12345,
            "rssi": -50
        }
        """
        url = f"{self._base_url}/api/status"
        try:
            async with self._session.get(
                url, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    _LOGGER.debug("Module status: %s", data)
                    return data
                _LOGGER.warning("Module /api/status returned HTTP %s", resp.status)
        except aiohttp.ClientError as err:
            _LOGGER.warning("Module /api/status error: %s", err)
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("Unexpected error fetching /api/status: %s", err)
        return None

    async def async_get_system(self) -> Optional[Dict[str, Any]]:
        """Fetch /api/system from the module.

        Returns a dict like:
        {
            "system": {
                "firmwareVersion": 1,
                "cpuFrequency": "240 MHz",
                "ip": "192.168.1.123",
                "currentDateTime": "2026-04-23 20:15:00",
                "upSince": "2026-04-22 11:03:18",
                "wifiStrength": -58
            }
        }
        """
        url = f"{self._base_url}/api/system"
        try:
            async with self._session.get(
                url, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    _LOGGER.debug("Module system info: %s", data)
                    return data
                _LOGGER.warning("Module /api/system returned HTTP %s", resp.status)
        except aiohttp.ClientError as err:
            _LOGGER.warning("Module /api/system error: %s", err)
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("Unexpected error fetching /api/system: %s", err)
        return None

    async def async_set_heat(self, percentage: int) -> bool:
        """Set heating percentage via /api/heat?percentage=XX (0-100)."""
        percentage = max(0, min(100, int(percentage)))
        url = f"{self._base_url}/api/heat"
        try:
            async with self._session.get(
                url,
                params={"percentage": percentage},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    _LOGGER.debug("Set heating to %s%%", percentage)
                    return True
                _LOGGER.warning(
                    "Module /api/heat?percentage=%s returned HTTP %s",
                    percentage,
                    resp.status,
                )
        except aiohttp.ClientError as err:
            _LOGGER.warning("Module /api/heat error: %s", err)
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("Unexpected error calling /api/heat: %s", err)
        return False

    async def async_test_connection(self) -> bool:
        """Test connectivity by fetching /api/status."""
        status = await self.async_get_status()
        return status is not None
