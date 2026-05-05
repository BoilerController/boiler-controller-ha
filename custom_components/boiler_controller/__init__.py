"""Boiler Controller Home Assistant integration."""
from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.loader import async_get_integration

from .const import DOMAIN, PLATFORMS, VERSION
from .controller import BoilerController

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Boiler Controller from a config entry."""
    _LOGGER.info("Setting up Boiler Controller")

    try:
        integration = await async_get_integration(hass, DOMAIN)
        integration_version = str(integration.version)
    except Exception as err:  # pylint: disable=broad-except
        _LOGGER.warning("Could not get integration version: %s", err)
        integration_version = VERSION

    controller = BoilerController(hass, entry, integration_version)
    success = await controller.async_start()
    if not success:
        _LOGGER.warning("Boiler Controller started with warnings – will retry")

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {"controller": controller}

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.info("Boiler Controller setup completed")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading Boiler Controller")

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    controller_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if controller_data:
        controller = controller_data.get("controller")
        if controller:
            await controller.async_stop()

    if DOMAIN in hass.data and entry.entry_id in hass.data[DOMAIN]:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
