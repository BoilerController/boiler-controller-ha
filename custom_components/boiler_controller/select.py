"""Select entities for the Boiler Controller integration."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, DIMMER_MODES


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up select entities for this config entry."""
    controller = hass.data[DOMAIN][config_entry.entry_id]["controller"]
    async_add_entities([BoilerControllerModeSelect(hass, config_entry, controller)])


class BoilerControllerModeSelect(SelectEntity):
    """Select entity toggling automatic/manual dimming."""

    _attr_should_poll = False
    _attr_options = DIMMER_MODES
    _attr_icon = "mdi:lightning-bolt-outline"

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry, controller) -> None:
        self.hass = hass
        self.config_entry = config_entry
        self.controller = controller
        self._attr_name = f"{config_entry.title} Dimmer Mode"
        self._attr_unique_id = f"{config_entry.entry_id}_dimmer_mode"
        self._attr_current_option = controller.dimming_mode
        self._remove_dispatcher = None

    async def async_added_to_hass(self) -> None:
        self._remove_dispatcher = async_dispatcher_connect(
            self.hass,
            self.controller.get_dimming_mode_signal(),
            self._handle_mode_update,
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._remove_dispatcher:
            self._remove_dispatcher()
            self._remove_dispatcher = None

    @callback
    def _handle_mode_update(self, mode: str) -> None:
        self._attr_current_option = mode
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        await self.controller.async_set_dimming_mode(option)

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.config_entry.entry_id)},
            "name": self.config_entry.title,
            "manufacturer": "Boiler Controller",
            "model": "P1 to Shelly Controller",
            "sw_version": self.controller.integration_version or str(self.config_entry.version),
        }
