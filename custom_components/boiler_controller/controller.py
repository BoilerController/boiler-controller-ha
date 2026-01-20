import logging
import asyncio

from homeassistant.core import HomeAssistant, Event, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    CONF_P1_TOTAL_ENTITY,
    CONF_SHELLY_URL,
    CONF_SHELLY_ID,
    CONF_SHELLY_POLL_INTERVAL,
    DEFAULT_MIN_DIMMER_VALUE,
    DEFAULT_MAX_DIMMER_VALUE,
    DEFAULT_CALCULATOR_MIN_INTERVAL,
    DEFAULT_SHELLY_POLL_INTERVAL,
    DEFAULT_MANUAL_BRIGHTNESS,
    DIMMER_MODE_AUTO,
    DIMMER_MODE_MANUAL,
    DIMMER_MODES,
)
from .shelly_client import ShellyClient
from .calculator import Calculator

_LOGGER = logging.getLogger(__name__)


class BoilerController:
    """Controller for managing boiler based on P1 data."""
    
    def __init__(self, hass: HomeAssistant, config_entry, integration_version: str | None):
        self.hass = hass
        self.config_entry = config_entry
        self.integration_version = integration_version
        self._cancel_listener = None
        self._poll_task = None
        self._last_dimmer_update = None
        self._last_power_value = None
        self._last_calculator_run = None
        self._shelly_status = None
        self._dispatcher_signal = f"{DOMAIN}_{config_entry.entry_id}_shelly_status"
        self._mode_signal = f"{DOMAIN}_{config_entry.entry_id}_dimming_mode"
        self._manual_brightness_signal = f"{DOMAIN}_{config_entry.entry_id}_manual_brightness"
        
        # Configuration
        self.shelly_url = config_entry.data[CONF_SHELLY_URL]
        self.power_sensor_id = config_entry.data[CONF_P1_TOTAL_ENTITY]
        self.shelly_client = ShellyClient(hass, self.shelly_url)
        self._calculator = Calculator()
        stored_mode = config_entry.options.get("dimming_mode", DIMMER_MODE_MANUAL)
        self._dimming_mode = stored_mode if stored_mode in DIMMER_MODES else DIMMER_MODE_MANUAL
        stored_manual = config_entry.options.get("manual_brightness", DEFAULT_MANUAL_BRIGHTNESS)
        self._manual_brightness = max(0, min(100, int(stored_manual)))
        
        # Options
        self.min_dimmer_value = config_entry.options.get("min_dimmer_value", DEFAULT_MIN_DIMMER_VALUE)
        self.max_dimmer_value = config_entry.options.get("max_dimmer_value", DEFAULT_MAX_DIMMER_VALUE)
        self._device_min_dimmer_value: int | None = None
        self._device_max_dimmer_value: int | None = None
        self._effective_min_dimmer_value = self.min_dimmer_value
        self._effective_max_dimmer_value = self.max_dimmer_value
        self.shelly_poll_interval = config_entry.options.get(
            CONF_SHELLY_POLL_INTERVAL,
            config_entry.data.get(CONF_SHELLY_POLL_INTERVAL, DEFAULT_SHELLY_POLL_INTERVAL)
        )
        
        self._recompute_effective_dimmer_bounds()
        
        _LOGGER.debug(
            "Initialized BoilerController: Power Sensor=%s, Shelly URL=%s, throttle_interval=%ds, poll_interval=%ds",
            self.power_sensor_id,
            self.shelly_url,
            DEFAULT_CALCULATOR_MIN_INTERVAL,
            self.shelly_poll_interval,
        )

    async def async_start(self):
        """Start the controller."""
        _LOGGER.info("Starting Boiler Controller")
        
        # Validate entities exist (informational only, always continue)
        await self._validate_configuration()

        # Test Shelly connection once at startup
        if await self.shelly_client.async_test_connection():
            _LOGGER.info("Shelly device reachable at %s", self.shelly_url)
            await self._async_sync_shelly_dimmer_constraints()
            await self._ensure_device_identity()
        else:
            _LOGGER.warning("Unable to reach Shelly device at %s during startup", self.shelly_url)

        # Start listening to power sensor state changes
        self._cancel_listener = async_track_state_change_event(
            self.hass,
            [self.power_sensor_id],
            self._async_power_sensor_changed
        )
        _LOGGER.info("Started listening to power sensor state changes for: %s", self.power_sensor_id)

        # Start Shelly polling task
        self._poll_task = self.hass.loop.create_task(self._async_poll_shelly())
        _LOGGER.info("Started Shelly polling task with interval %ss", self.shelly_poll_interval)
        
        # Run initial update (will fail gracefully if entities don't exist yet)
        await self._async_update()
        
        _LOGGER.info("Boiler Controller started successfully")
        return True

    @callback
    async def _async_power_sensor_changed(self, event: Event):
        """Handle power sensor state changes."""
        now = dt_util.utcnow()
        if self._last_calculator_run is not None:
            elapsed = (now - self._last_calculator_run).total_seconds()
            if elapsed < DEFAULT_CALCULATOR_MIN_INTERVAL:
                return

        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")

        if self._dimming_mode != DIMMER_MODE_AUTO:
            _LOGGER.debug(
                "Ignoring power sensor event while in manual mode: %s -> %s",
                old_state.state if old_state else "None",
                new_state.state if new_state else "None",
            )
            return

        if not new_state:
            return
            
        # Skip if state hasn't actually changed or is unavailable
        if (old_state and new_state.state == old_state.state) or new_state.state in ("unknown", "unavailable", "none"):
            _LOGGER.debug("Skipping update - state unchanged or unavailable")
            return
            
        # Parse and validate the new power value first
        try:
            raw_power_value = float(new_state.state)
        except (ValueError, TypeError):
            _LOGGER.warning("Invalid power sensor value: %s", new_state.state)
            return

        unit = self._get_state_unit(new_state)
        new_power_value = self._normalize_power_unit(raw_power_value, unit)
            
        # Only update if power value actually changed significantly (more than 1W difference)
        # TODO: Consider making this threshold configurable as the controller needs more than 200 watt to perform well
        if self._last_power_value is not None and abs(new_power_value - self._last_power_value) < 1:
            _LOGGER.debug("Skipping update - power change too small: %.1fW", abs(new_power_value - self._last_power_value))
            return
            
        # Store the new power value
        self._last_power_value = new_power_value
            
        _LOGGER.debug(
            "Power sensor changed from %s %s to %.3f W (processing update)",
            old_state.state if old_state else "unknown",
            unit or "W",
            new_power_value,
        )
        
        # Update the controller with new power value
        await self._async_update()

    async def async_stop(self):
        """Stop the controller."""
        _LOGGER.info("Stopping Boiler Controller")
        if self._cancel_listener:
            self._cancel_listener()
            self._cancel_listener = None
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

    async def _validate_configuration(self) -> bool:
        """Validate that all configured entities exist."""
        
        # Check power sensor exists (informational only, don't block startup)
        power_state = self.hass.states.get(self.power_sensor_id)
        if not power_state:
            _LOGGER.info("Power sensor %s not found yet - controller will start and wait for entity", self.power_sensor_id)
        else:
            _LOGGER.info("Found power sensor: %s (current value: %s)", self.power_sensor_id, power_state.state)
        
        _LOGGER.info(
            "Controller configured with power sensor: %s, Shelly URL: %s",
            self.power_sensor_id,
            self.shelly_url,
        )
        
        return True

    async def _async_update(self, *args):
        """Update the controller - read P1 data and adjust dimmer."""
        try:
            # Get current power consumption/production from P1
            power_value = await self._get_p1_power_value()
            if power_value is None:
                _LOGGER.debug("Could not read P1 power value - sensor may not be ready yet")
                return
                
            # Store the current power value
            self._last_power_value = power_value
            _LOGGER.debug("Current P1 power value: %s W", power_value)

            if self._dimming_mode == DIMMER_MODE_MANUAL:
                _LOGGER.debug("Manual dimmer mode active - skipping automatic adjustment")
                return
            
            # Calculate dimmer percentage based on power value
            dimmer_percentage = self._calculator.calculate(
                power_value,
                self._effective_min_dimmer_value,
                self._effective_max_dimmer_value,
                boiler_consumption=self._extract_boiler_consumption(),
            )
            _LOGGER.debug("Calculated dimmer percentage: %s%%", dimmer_percentage)
            
            # Update dimmer
            await self._set_dimmer_percentage(dimmer_percentage, source=DIMMER_MODE_AUTO)
            
            timestamp = dt_util.utcnow()
            self._last_calculator_run = timestamp
            self._last_dimmer_update = timestamp
            
        except Exception as err:
            _LOGGER.error("Error during controller update: %s", err)

    async def _get_p1_power_value(self) -> float | None:
        """Get current power value from power sensor entity."""
        try:
            state = self.hass.states.get(self.power_sensor_id)
            if not state:
                # Only show this error occasionally, not every time
                now = dt_util.utcnow()
                if not hasattr(self, '_last_missing_sensor_log') or \
                   (now - self._last_missing_sensor_log).total_seconds() > 60:
                    _LOGGER.warning("Power sensor %s not found - check if entity exists", self.power_sensor_id)
                    self._last_missing_sensor_log = now
                return None
                
            if state.state in ("unknown", "unavailable", "none"):
                _LOGGER.debug("Power sensor %s is unavailable (state: %s)", self.power_sensor_id, state.state)
                return None
                
            # Convert state to float and normalize units
            power_value = float(state.state)
            unit = self._get_state_unit(state)
            power_value = self._normalize_power_unit(power_value, unit)
            # Clear the missing sensor log timer since we got data
            if hasattr(self, '_last_missing_sensor_log'):
                delattr(self, '_last_missing_sensor_log')
            return power_value
            
        except (ValueError, TypeError) as err:
            _LOGGER.warning("Error parsing power sensor value '%s': %s", state.state if state else "None", err)
            return None

    async def _async_poll_shelly(self):
        """Poll Shelly status at the configured interval."""
        while True:
            try:
                status = await self.shelly_client.async_get_status()
                if status is not None:
                    self._shelly_status = status
                    async_dispatcher_send(self.hass, self._dispatcher_signal, status)
                await asyncio.sleep(self.shelly_poll_interval)
            except asyncio.CancelledError:
                _LOGGER.debug("Shelly polling task cancelled")
                break
            except Exception as err:  # pylint: disable=broad-except
                _LOGGER.error("Unexpected Shelly polling error: %s", err)
                await asyncio.sleep(self.shelly_poll_interval)

    async def _async_refresh_shelly_status(self):
        """Force a Shelly status refresh outside the poll loop."""
        try:
            status = await self.shelly_client.async_get_status()
            if status is None:
                return
            self._shelly_status = status
            async_dispatcher_send(self.hass, self._dispatcher_signal, status)
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.debug("Manual Shelly status refresh failed: %s", err)

    async def _set_dimmer_percentage(self, percentage: int, *, source: str = DIMMER_MODE_AUTO):
        """Set the dimmer to the specified percentage using Shelly API."""
        try:
            context = "manual override" if source == DIMMER_MODE_MANUAL else "auto calculation"
            if percentage <= 0:
                _LOGGER.info("Shelly dimmer request (%s): turn off (requested %s%%)", context, percentage)
                set_success = await self.shelly_client.async_set_brightness(0)
                if not set_success:
                    _LOGGER.warning("Failed to set Shelly dimmer to 0%% before turn off")
                success = await self.shelly_client.async_turn_off()
                if success:
                    _LOGGER.debug("Shelly dimmer turned off")
                else:
                    _LOGGER.warning("Failed to turn off Shelly dimmer")
                _LOGGER.info("Shelly dimmer turn_off success=%s", success)
            else:
                if source == DIMMER_MODE_MANUAL:
                    _LOGGER.info(
                        "Shelly dimmer request (%s): set to %s%%",
                        context,
                        percentage,
                    )
                else:
                    _LOGGER.info(
                        "Shelly dimmer request (%s): set to %s%% (effective range %s-%s%%)",
                        context,
                        percentage,
                        self._effective_min_dimmer_value,
                        self._effective_max_dimmer_value,
                    )
                success = await self.shelly_client.async_set_brightness(percentage)
                if success:
                    _LOGGER.debug("Shelly dimmer set to %s%%", percentage)
                else:
                    _LOGGER.warning("Failed to set Shelly dimmer to %s%%", percentage)
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("Error setting Shelly dimmer percentage: %s", err)

    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self.config_entry.entry_id)},
            "name": self.config_entry.title,
            "manufacturer": "Boiler Controller",
            "model": "P1 to Dimmer Controller",
            "sw_version": self.integration_version or str(self.config_entry.version),
        }

    def get_status(self):
        """Get current controller status."""
        return {
            "last_dimmer_update": self._last_dimmer_update,
            "last_power_value": self._last_power_value,
            "power_sensor": self.power_sensor_id,
            "shelly_url": self.shelly_url,
            "shelly_status": self._shelly_status,
            "update_method": "event_driven",
            "calculator_min_interval": DEFAULT_CALCULATOR_MIN_INTERVAL,
            "shelly_poll_interval": self.shelly_poll_interval,
            "min_dimmer": self.min_dimmer_value,
            "max_dimmer": self.max_dimmer_value,
            "device_min_dimmer": self._device_min_dimmer_value,
            "device_max_dimmer": self._device_max_dimmer_value,
            "effective_min_dimmer": self._effective_min_dimmer_value,
            "effective_max_dimmer": self._effective_max_dimmer_value,
            "dimming_mode": self._dimming_mode,
            "manual_brightness": self._manual_brightness,
        }

    def get_shelly_status(self):
        """Expose latest Shelly polling data."""
        return self._shelly_status

    def get_shelly_status_signal(self):
        """Return dispatcher signal name for Shelly status updates."""
        return self._dispatcher_signal

    def get_dimming_mode_signal(self):
        """Dispatcher signal for dimming mode changes."""
        return self._mode_signal

    def get_manual_brightness_signal(self):
        """Dispatcher signal for manual brightness changes."""
        return self._manual_brightness_signal

    @property
    def dimming_mode(self) -> str:
        return self._dimming_mode

    @property
    def manual_brightness(self) -> int:
        return self._manual_brightness

    async def async_set_dimming_mode(self, mode: str):
        """Set dimming mode to auto or manual."""
        if mode not in (DIMMER_MODE_AUTO, DIMMER_MODE_MANUAL):
            raise ValueError(f"Unsupported dimming mode: {mode}")
        if mode == self._dimming_mode:
            return

        self._dimming_mode = mode
        self._persist_controller_options(dimming_mode=mode)
        async_dispatcher_send(self.hass, self._mode_signal, mode)

        if mode == DIMMER_MODE_MANUAL:
            await self._apply_manual_brightness()
        else:
            await self._async_update()

    async def async_set_manual_brightness(self, brightness: int):
        """Store manual brightness and apply when manual mode is active."""
        brightness = max(0, min(100, int(brightness)))
        if brightness == self._manual_brightness:
            return
        self._manual_brightness = brightness
        self._persist_controller_options(manual_brightness=self._manual_brightness)
        async_dispatcher_send(self.hass, self._manual_brightness_signal, brightness)

        if self._dimming_mode == DIMMER_MODE_MANUAL:
            await self._apply_manual_brightness()

    async def _apply_manual_brightness(self):
        """Apply the stored manual brightness to the Shelly device."""
        _LOGGER.debug("Applying manual brightness override: %s%%", self._manual_brightness)
        await self._set_dimmer_percentage(self._manual_brightness, source=DIMMER_MODE_MANUAL)
        self._last_dimmer_update = dt_util.utcnow()
        await self._async_refresh_shelly_status()

    def _extract_boiler_consumption(self) -> float:
        """Return the latest Shelly-reported consumption in watts."""
        status = self._shelly_status or {}
        if not status:
            return 0.0
        value = status.get("apower", status.get("power"))
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    async def _ensure_device_identity(self) -> None:
        """Persist the Shelly device identifier on the config entry when missing."""
        if self.config_entry.data.get(CONF_SHELLY_ID):
            return

        device_info = await self.shelly_client.async_get_device_info()
        if not device_info:
            _LOGGER.debug("Shelly device info unavailable for %s", self.shelly_url)
            return

        device_id = ShellyClient.extract_device_id(device_info)
        if not device_id:
            _LOGGER.debug("Shelly device info missing identifier for %s", self.shelly_url)
            return

        new_data = dict(self.config_entry.data)
        new_data[CONF_SHELLY_ID] = device_id
        self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
        _LOGGER.info(
            "Stored Shelly device id %s for entry %s",
            device_id,
            self.config_entry.entry_id,
        )

    def _persist_controller_options(self, **updates):
        """Store controller runtime preferences in the config entry options."""
        if not updates:
            return

        new_options = dict(self.config_entry.options)
        changed = False
        for key, value in updates.items():
            if value is None:
                continue
            if new_options.get(key) == value:
                continue
            new_options[key] = value
            changed = True

        if changed:
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                options=new_options,
            )

    async def _async_sync_shelly_dimmer_constraints(self):
        """Fetch Shelly light config to honor hardware brightness bounds."""
        config = await self.shelly_client.async_get_light_config()
        if not config:
            _LOGGER.debug("Could not load Shelly light config; using user dimmer bounds")
            return

        device_min = self._extract_brightness_limit(config, limit_type="min")
        device_max = self._extract_brightness_limit(config, limit_type="max")

        if device_min is not None:
            self._device_min_dimmer_value = max(0, min(100, device_min))
        if device_max is not None:
            self._device_max_dimmer_value = max(0, min(100, device_max))

        self._recompute_effective_dimmer_bounds()

    def _recompute_effective_dimmer_bounds(self):
        """Intersect user preferences with Shelly limits to get the enforceable range.

        The controller never sends a brightness outside this window, so this method
        re-evaluates the currently valid minimum and maximum whenever either the
        user-configured bounds or the device-reported constraints change.
        """

        new_min = self.min_dimmer_value  # Start from the configured preference
        if self._device_min_dimmer_value is not None:
            new_min = max(new_min, self._device_min_dimmer_value)

        new_max = self.max_dimmer_value  # Start from the configured preference
        if self._device_max_dimmer_value is not None:
            new_max = min(new_max, self._device_max_dimmer_value)

        if new_max < new_min:
            new_max = new_min

        if (new_min != self._effective_min_dimmer_value) or (new_max != self._effective_max_dimmer_value):
            _LOGGER.info(
                "Effective dimmer bounds updated: min=%s%%, max=%s%% (user min=%s%%, user max=%s%%, device min=%s%%, device max=%s%%)",
                new_min,
                new_max,
                self.min_dimmer_value,
                self.max_dimmer_value,
                self._device_min_dimmer_value,
                self._device_max_dimmer_value,
            )

        self._effective_min_dimmer_value = new_min
        self._effective_max_dimmer_value = new_max

    @staticmethod
    def _extract_brightness_limit(config: dict, *, limit_type: str) -> int | None:
        """Search Shelly config for brightness min/max values."""
        assert limit_type in {"min", "max"}
        matches: list[int] = []

        def _search(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    key_lower = key.lower()
                    if "bright" in key_lower and limit_type in key_lower and isinstance(value, (int, float)):
                        matches.append(int(value))
                    else:
                        _search(value)
            elif isinstance(node, list):
                for item in node:
                    _search(item)

        _search(config)

        if not matches:
            return None

        return min(matches) if limit_type == "min" else max(matches)

    @staticmethod
    def _get_state_unit(state) -> str:
        """Fetch unit from state attributes, falling back to native unit."""
        if not state:
            return ""
        unit = state.attributes.get("unit_of_measurement")
        if not unit:
            unit = state.attributes.get("native_unit_of_measurement")
        if isinstance(unit, str):
            return unit
        return str(unit) if unit is not None else ""

    @staticmethod
    def _normalize_power_unit(power_value: float, unit: str) -> float:
        """Convert incoming power readings to watts."""
        if not unit:
            return power_value

        cleaned = unit.strip().lower()
        # Handle variations like kW, kilo watt, kilowatt, etc.
        if cleaned.startswith("kw") or "kilowatt" in cleaned:
            return power_value * 1000
        # No conversion needed for W-based units
        return power_value