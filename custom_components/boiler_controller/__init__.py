import logging

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.loader import async_get_integration

from .const import DOMAIN, PLATFORMS
from .controller import BoilerController

_LOGGER = logging.getLogger(__name__)


# Set up the component
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Boiler Controller from a config entry."""
    _LOGGER.info("Setting up Boiler Controller")
    
    integration = await async_get_integration(hass, DOMAIN)
    integration_version = integration.version

    # Create the controller
    controller = BoilerController(hass, entry, integration_version)
    
    # Start the controller (now handles missing entities gracefully)
    success = await controller.async_start()
    if not success:
        _LOGGER.error("Failed to start Boiler Controller")
        # Don't raise ConfigEntryNotReady anymore - let it start and wait for entities
        _LOGGER.warning("Boiler Controller will continue running and wait for entities to become available")
    
    # Store the controller
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "controller": controller,
    }
    
    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    _LOGGER.info("Boiler Controller setup completed")
    return True

# Implement unloading and reloading of the config entry
async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading Boiler Controller")
    
    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    # Stop the controller
    controller_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if controller_data:
        controller = controller_data.get("controller")
        if controller:
            await controller.async_stop()
    
    # Remove from hass.data
    if DOMAIN in hass.data and entry.entry_id in hass.data[DOMAIN]:
        hass.data[DOMAIN].pop(entry.entry_id)
    
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
