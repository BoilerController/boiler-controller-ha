"""Image entity exposing the calibration profile curve."""
from __future__ import annotations

from typing import Callable

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    controller = hass.data[DOMAIN][config_entry.entry_id]["controller"]
    async_add_entities([BoilerControllerProfileImage(hass, controller, config_entry)])


class BoilerControllerProfileImage(ImageEntity):
    """Image entity showing the latest calibration curve."""

    _attr_content_type = "image/svg+xml"
    _attr_has_entity_name = True

    def __init__(self, hass: HomeAssistant, controller, config_entry: ConfigEntry) -> None:
        super().__init__(hass)
        self._controller = controller
        self._attr_unique_id = f"{config_entry.entry_id}_calibration_curve"
        self._attr_name = "Calibration Curve"
        self._manager = controller.profile_image_manager
        self._attr_entity_picture_local = self._manager.local_url
        self._attr_device_info = controller.device_info
        self._attr_image_last_updated = controller.get_profile_image_updated_at()
        self._unsub_dispatcher: Callable[[], None] | None = None

    async def async_image(self) -> bytes | None:
        data = await self._manager.async_get_bytes()
        if data is None:
            # Render the default curve if nothing exists yet.
            await self._manager.async_update(self._controller.get_active_plot_points())
            data = await self._manager.async_get_bytes()
            if data is not None:
                self._attr_image_last_updated = dt_util.utcnow()
                self.async_write_ha_state()
        return data

    @property
    def available(self) -> bool:
        return True

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        async def _handle_image_refresh() -> None:
            self._attr_image_last_updated = dt_util.utcnow()
            self.async_write_ha_state()

        signal = self._controller.get_profile_image_signal()
        self._unsub_dispatcher = async_dispatcher_connect(self.hass, signal, _handle_image_refresh)

    async def async_will_remove_from_hass(self) -> None:
        await super().async_will_remove_from_hass()
        if self._unsub_dispatcher:
            self._unsub_dispatcher()
            self._unsub_dispatcher = None