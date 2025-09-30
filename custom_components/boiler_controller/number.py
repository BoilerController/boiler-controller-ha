"""Number entities for controlling manual brightness."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up number entities for this config entry."""
    controller = hass.data[DOMAIN][config_entry.entry_id]["controller"]
    async_add_entities([BoilerControllerManualBrightnessNumber(hass, config_entry, controller)])


class BoilerControllerManualBrightnessNumber(NumberEntity):
    """Number entity exposing manual brightness override."""

    _attr_should_poll = False
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:brightness-percent"

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry, controller) -> None:
        self.hass = hass
        self.config_entry = config_entry
        self.controller = controller
        self._attr_name = f"{config_entry.title} Manual Brightness"
        self._attr_unique_id = f"{config_entry.entry_id}_manual_brightness"
        self._attr_native_value = controller.manual_brightness
        self._remove_dispatcher = None

    async def async_added_to_hass(self) -> None:
        self._remove_dispatcher = async_dispatcher_connect(
            self.hass,
            self.controller.get_manual_brightness_signal(),
            self._handle_manual_brightness_update,
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._remove_dispatcher:
            self._remove_dispatcher()
            self._remove_dispatcher = None

    @callback
    def _handle_manual_brightness_update(self, value: int) -> None:
        self._attr_native_value = value
        self.async_write_ha_state()

    async def async_set_native_value(self, value: float) -> None:
        await self.controller.async_set_manual_brightness(int(value))

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.config_entry.entry_id)},
            "name": self.config_entry.title,
            "manufacturer": "Boiler Controller",
            "model": "P1 to Shelly Controller",
            "sw_version": self.controller.integration_version or str(self.config_entry.version),
        }
