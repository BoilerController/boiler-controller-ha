"""Select entity for toggling auto/manual control mode."""
from __future__ import annotations

from typing import Callable, List

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CONTROL_MODES


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    controller = hass.data[DOMAIN][config_entry.entry_id]["controller"]
    async_add_entities([BoilerControlModeSelect(hass, config_entry, controller)])


class BoilerControlModeSelect(SelectEntity):
    """Select entity to switch between automatic and manual heating control."""

    _attr_should_poll = False
    _attr_options = CONTROL_MODES
    _attr_icon = "mdi:auto-mode"

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry, controller
    ) -> None:
        self.hass = hass
        self.config_entry = config_entry
        self.controller = controller
        self._attr_name = f"{config_entry.title} Control Mode"
        self._attr_unique_id = f"{config_entry.entry_id}_control_mode"
        self._attr_current_option = controller.control_mode
        self._remove_callbacks: List[Callable[[], None]] = []

    async def async_added_to_hass(self) -> None:
        self._remove_callbacks.append(
            async_dispatcher_connect(
                self.hass,
                self.controller.get_control_mode_signal(),
                self._handle_mode_update,
            )
        )
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        for remove in self._remove_callbacks:
            remove()
        self._remove_callbacks.clear()

    @callback
    def _handle_mode_update(self, mode: str) -> None:
        self._attr_current_option = mode
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        await self.controller.async_set_control_mode(option)

    @property
    def device_info(self):
        from .const import VERSION as DEFAULT_VERSION
        version = self.controller.integration_version or DEFAULT_VERSION
        return {
            "identifiers": {(DOMAIN, self.config_entry.entry_id)},
            "name": self.config_entry.title,
            "manufacturer": "Boiler Controller",
            "model": "Boiler Controller Module",
            "sw_version": str(version),
        }
