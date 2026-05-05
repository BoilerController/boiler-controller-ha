"""Number entity for manual heating percentage override."""
from __future__ import annotations

from typing import Callable, List

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MIN_MANUAL_PERCENTAGE, CONTROL_MODE_MANUAL


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    controller = hass.data[DOMAIN][config_entry.entry_id]["controller"]
    async_add_entities(
        [BoilerManualPercentageNumber(hass, config_entry, controller)]
    )


class BoilerManualPercentageNumber(NumberEntity):
    """Number entity for the manual heating percentage (0-100 %)."""

    _attr_should_poll = False
    _attr_native_min_value = MIN_MANUAL_PERCENTAGE
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:thermometer"
    _attr_native_unit_of_measurement = "%"

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry, controller
    ) -> None:
        self.hass = hass
        self.config_entry = config_entry
        self.controller = controller
        self._attr_name = f"{config_entry.title} Manual Heating"
        self._attr_unique_id = f"{config_entry.entry_id}_manual_heating"
        self._attr_native_value = controller.manual_percentage
        self._remove_callbacks: List[Callable[[], None]] = []

    async def async_added_to_hass(self) -> None:
        self._remove_callbacks.append(
            async_dispatcher_connect(
                self.hass,
                self.controller.get_manual_pct_signal(),
                self._handle_manual_pct_update,
            )
        )
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
    def _handle_manual_pct_update(self, value: int) -> None:
        self._attr_native_value = value
        self.async_write_ha_state()

    @callback
    def _handle_mode_update(self, _mode: str) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self.controller.control_mode == CONTROL_MODE_MANUAL

    async def async_set_native_value(self, value: float) -> None:
        await self.controller.async_set_manual_percentage(int(value))

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
