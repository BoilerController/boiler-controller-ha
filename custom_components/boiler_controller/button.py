"""Button entities for the Boiler Controller integration."""
from __future__ import annotations

import logging
from typing import Any, Dict

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def _device_info(config_entry: ConfigEntry, controller) -> Dict[str, Any]:
    from .const import VERSION
    version = controller.integration_version or VERSION
    return {
        "identifiers": {(DOMAIN, config_entry.entry_id)},
        "name": config_entry.title,
        "manufacturer": "Boiler Controller",
        "model": "Boiler Controller Module",
        "sw_version": str(version),
    }


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    controller = hass.data[DOMAIN][config_entry.entry_id]["controller"]
    async_add_entities(
        [
            BoilerStopButton(hass, config_entry, controller),
        ],
        True,
    )


class BoilerStopButton(ButtonEntity):
    """Button that immediately sets the boiler heating to 0%."""

    _attr_should_poll = False
    _attr_icon = "mdi:stop-circle-outline"

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry, controller
    ) -> None:
        self.hass = hass
        self.config_entry = config_entry
        self.controller = controller
        self._attr_name = f"{config_entry.title} Stop Heating"
        self._attr_unique_id = f"{config_entry.entry_id}_stop_heating"

    async def async_press(self) -> None:
        """Stop boiler heating immediately."""
        _LOGGER.info("Stop heating button pressed")
        await self.controller.boiler_client.async_set_heat(0)

    @property
    def device_info(self) -> Dict[str, Any]:
        return _device_info(self.config_entry, self.controller)
