"""Controller for the Boiler Controller integration."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from homeassistant.core import HomeAssistant, Event, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    CONF_P1_TOTAL_ENTITY,
    CONF_BOILER_HOST,
    CONF_POLL_INTERVAL,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_MAX_BOILER_WATTS,
    DEFAULT_MAX_STEP_PERCENTAGE,
    DEFAULT_CONTROLLER_MIN_INTERVAL,
    DEFAULT_MANUAL_PERCENTAGE,
    MIN_MANUAL_PERCENTAGE,
    CONTROL_MODE_AUTO,
    CONTROL_MODE_MANUAL,
    CONTROL_MODES,
)
from .boiler_client import BoilerClient
from .calculator import Calculator

_LOGGER = logging.getLogger(__name__)


class BoilerController:
    """Controller for managing a boiler based on P1 surplus power."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry,
        integration_version: str | None,
    ) -> None:
        self.hass = hass
        self.config_entry = config_entry
        self.integration_version = integration_version

        # Dispatcher signals for entity updates
        self._status_signal = f"{DOMAIN}_{config_entry.entry_id}_status"
        self._mode_signal = f"{DOMAIN}_{config_entry.entry_id}_control_mode"
        self._manual_pct_signal = f"{DOMAIN}_{config_entry.entry_id}_manual_pct"

        # Internal state
        self._cancel_listener = None
        self._poll_task: asyncio.Task | None = None
        self._last_control_run: Any = None
        self._last_power_value: float | None = None
        self._module_status: Dict[str, Any] | None = None
        self._module_system: Dict[str, Any] | None = None
        self._last_update: Any = None

        # Configuration
        self.power_sensor_id: str = config_entry.data[CONF_P1_TOTAL_ENTITY]
        boiler_host: str = config_entry.data[CONF_BOILER_HOST]
        self.boiler_client = BoilerClient(hass, boiler_host)

        self.poll_interval: int = int(
            config_entry.options.get(
                CONF_POLL_INTERVAL,
                config_entry.data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
            )
        )
        self.max_boiler_watts: float = float(DEFAULT_MAX_BOILER_WATTS)
        self._calculator = Calculator(
            max_boiler_watts=self.max_boiler_watts,
            max_step=DEFAULT_MAX_STEP_PERCENTAGE,
        )

        # Control mode (auto / manual)
        stored_mode = config_entry.options.get("control_mode", CONTROL_MODE_AUTO)
        self._control_mode: str = (
            stored_mode if stored_mode in CONTROL_MODES else CONTROL_MODE_AUTO
        )
        stored_manual = config_entry.options.get(
            "manual_percentage", DEFAULT_MANUAL_PERCENTAGE
        )
        self._manual_percentage: int = max(
            MIN_MANUAL_PERCENTAGE, min(100, int(stored_manual))
        )

        _LOGGER.debug(
            "Initialized BoilerController: power_sensor=%s, boiler_host=%s, "
            "poll_interval=%ds, max_boiler_watts=%.0fW",
            self.power_sensor_id,
            boiler_host,
            self.poll_interval,
            self.max_boiler_watts,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_start(self) -> bool:
        """Start the controller."""
        _LOGGER.info("Starting Boiler Controller")

        await self._validate_configuration()

        if await self.boiler_client.async_test_connection():
            _LOGGER.info("Boiler module reachable at %s", self.boiler_client.host)
        else:
            _LOGGER.warning(
                "Unable to reach boiler module at %s during startup",
                self.boiler_client.host,
            )

        # Track power sensor changes for event-driven control
        self._cancel_listener = async_track_state_change_event(
            self.hass,
            [self.power_sensor_id],
            self._async_power_sensor_changed,
        )
        _LOGGER.info("Listening to power sensor: %s", self.power_sensor_id)

        # Start polling task for module telemetry
        self._poll_task = self.hass.loop.create_task(self._async_poll_module())
        _LOGGER.info("Started module polling task (interval %ss)", self.poll_interval)

        # Initial control update
        await self._async_update()

        _LOGGER.info("Boiler Controller started successfully")
        return True

    async def async_stop(self) -> None:
        """Stop the controller and release resources."""
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

    # ------------------------------------------------------------------
    # Power sensor callback
    # ------------------------------------------------------------------

    @callback
    async def _async_power_sensor_changed(self, event: Event) -> None:
        """Handle power sensor state changes."""
        now = dt_util.utcnow()
        if self._last_control_run is not None:
            elapsed = (now - self._last_control_run).total_seconds()
            if elapsed < DEFAULT_CONTROLLER_MIN_INTERVAL:
                return

        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")

        if self._control_mode != CONTROL_MODE_AUTO:
            return

        if not new_state or new_state.state in ("unknown", "unavailable", "none"):
            return

        if old_state and new_state.state == old_state.state:
            return

        try:
            raw = float(new_state.state)
        except (ValueError, TypeError):
            _LOGGER.warning("Invalid power sensor value: %s", new_state.state)
            return

        unit = new_state.attributes.get("unit_of_measurement", "")
        power_w = self._normalize_power_unit(raw, unit)

        if (
            self._last_power_value is not None
            and abs(power_w - self._last_power_value) < 1
        ):
            return

        self._last_power_value = power_w
        _LOGGER.debug("Power sensor changed: %.1f W", power_w)
        await self._async_update()

    # ------------------------------------------------------------------
    # Main control loop
    # ------------------------------------------------------------------

    async def _async_update(self, *_: Any) -> None:
        """Read P1 data and adjust boiler heating percentage when in auto mode."""
        try:
            power_value = await self._get_p1_power_value()
            if power_value is not None:
                self._last_power_value = power_value

            if self._control_mode == CONTROL_MODE_MANUAL:
                return

            if power_value is None:
                _LOGGER.debug("No P1 value available – skipping auto control")
                return

            # Current boiler state from last polled status
            current_boiler_watts: float = 0.0
            current_pct: int = 0
            if self._module_status:
                current_boiler_watts = float(
                    self._module_status.get("power", 0) or 0
                )
                current_pct = int(
                    self._module_status.get("heatingPercentage", 0) or 0
                )

            new_pct = self._calculator.calculate(
                grid_watts=power_value,
                current_percentage=current_pct,
                boiler_watts=current_boiler_watts,
            )

            if new_pct != current_pct:
                await self._set_heating_percentage(new_pct)

            self._last_control_run = dt_util.utcnow()
            self._last_update = self._last_control_run

        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("Error during controller update: %s", err)

    # ------------------------------------------------------------------
    # Module polling
    # ------------------------------------------------------------------

    async def _async_poll_module(self) -> None:
        """Poll module /api/status and /api/system at the configured interval."""
        while True:
            try:
                status = await self.boiler_client.async_get_status()
                if status is not None:
                    self._module_status = status

                system = await self.boiler_client.async_get_system()
                if system is not None:
                    self._module_system = system

                async_dispatcher_send(
                    self.hass,
                    self._status_signal,
                    {
                        "status": self._module_status,
                        "system": self._module_system,
                    },
                )

                await asyncio.sleep(self.poll_interval)

            except asyncio.CancelledError:
                _LOGGER.debug("Module polling task cancelled")
                break
            except Exception as err:  # pylint: disable=broad-except
                _LOGGER.error("Unexpected module polling error: %s", err)
                await asyncio.sleep(self.poll_interval)

    # ------------------------------------------------------------------
    # Heating control helpers
    # ------------------------------------------------------------------

    async def _set_heating_percentage(self, percentage: int) -> None:
        """Send heating percentage to the boiler module API."""
        percentage = max(0, min(100, int(percentage)))
        _LOGGER.info("Setting boiler heating to %d%%", percentage)
        success = await self.boiler_client.async_set_heat(percentage)
        if not success:
            _LOGGER.warning("Failed to set boiler heating to %d%%", percentage)

    # ------------------------------------------------------------------
    # Manual control (used by select / number entities)
    # ------------------------------------------------------------------

    async def async_set_control_mode(self, mode: str) -> None:
        """Switch between auto and manual control mode."""
        if mode not in CONTROL_MODES:
            raise ValueError(f"Unsupported control mode: {mode}")
        if mode == self._control_mode:
            return

        self._control_mode = mode
        self._persist_options(control_mode=mode)
        async_dispatcher_send(self.hass, self._mode_signal, mode)

        if mode == CONTROL_MODE_MANUAL:
            await self._set_heating_percentage(self._manual_percentage)
        else:
            await self._async_update()

    async def async_set_manual_percentage(self, percentage: int) -> None:
        """Store manual percentage and apply it when in manual mode."""
        percentage = max(MIN_MANUAL_PERCENTAGE, min(100, int(percentage)))
        if percentage == self._manual_percentage:
            return

        self._manual_percentage = percentage
        self._persist_options(manual_percentage=percentage)
        async_dispatcher_send(self.hass, self._manual_pct_signal, percentage)

        if self._control_mode == CONTROL_MODE_MANUAL:
            await self._set_heating_percentage(percentage)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _validate_configuration(self) -> None:
        state = self.hass.states.get(self.power_sensor_id)
        if not state:
            _LOGGER.info(
                "Power sensor %s not found yet – will wait", self.power_sensor_id
            )
        else:
            _LOGGER.info(
                "Power sensor %s found (current: %s)", self.power_sensor_id, state.state
            )

    async def _get_p1_power_value(self) -> float | None:
        """Return normalized W value from the configured P1 sensor entity."""
        state = self.hass.states.get(self.power_sensor_id)
        if not state:
            now = dt_util.utcnow()
            if not hasattr(self, "_last_missing_log") or (
                now - self._last_missing_log
            ).total_seconds() > 60:
                _LOGGER.warning("Power sensor %s not found", self.power_sensor_id)
                self._last_missing_log = now
            return None

        if state.state in ("unknown", "unavailable", "none"):
            return None

        try:
            raw = float(state.state)
        except (ValueError, TypeError):
            _LOGGER.warning("Cannot parse power sensor value: %s", state.state)
            return None

        unit = state.attributes.get(
            "unit_of_measurement",
            state.attributes.get("native_unit_of_measurement", ""),
        )
        return self._normalize_power_unit(raw, str(unit) if unit else "")

    @staticmethod
    def _normalize_power_unit(value: float, unit: str) -> float:
        """Convert kW to W when the unit indicates kilowatts."""
        if not unit:
            return value
        cleaned = unit.strip().lower()
        if cleaned.startswith("kw") or "kilowatt" in cleaned:
            return value * 1000
        return value

    def _persist_options(self, **kwargs: Any) -> None:
        """Merge kwargs into the config entry options."""
        current = dict(self.config_entry.options)
        current.update(kwargs)
        self.hass.config_entries.async_update_entry(
            self.config_entry, options=current
        )

    # ------------------------------------------------------------------
    # Public accessors used by platform entities
    # ------------------------------------------------------------------

    def get_status_signal(self) -> str:
        return self._status_signal

    def get_control_mode_signal(self) -> str:
        return self._mode_signal

    def get_manual_pct_signal(self) -> str:
        return self._manual_pct_signal

    @property
    def control_mode(self) -> str:
        return self._control_mode

    @property
    def manual_percentage(self) -> int:
        return self._manual_percentage

    def get_module_status(self) -> Dict[str, Any] | None:
        """Return the latest /api/status payload."""
        return self._module_status

    def get_module_system(self) -> Dict[str, Any] | None:
        """Return the latest /api/system payload (nested under 'system' key)."""
        return self._module_system

    def get_status(self) -> Dict[str, Any]:
        """Return a diagnostics summary of controller state."""
        calc_result = self._calculator.last_result
        return {
            "power_sensor": self.power_sensor_id,
            "boiler_host": self.boiler_client.host,
            "control_mode": self._control_mode,
            "manual_percentage": self._manual_percentage,
            "last_power_value": self._last_power_value,
            "last_update": self._last_update,
            "poll_interval": self.poll_interval,
            "max_boiler_watts": self.max_boiler_watts,
            "last_target_pct": calc_result.target_percentage if calc_result else None,
            "last_available_watts": calc_result.available_watts if calc_result else None,
        }

    @property
    def device_info(self) -> Dict[str, Any]:
        from .const import VERSION as DEFAULT_VERSION
        version = self.integration_version or DEFAULT_VERSION
        return {
            "identifiers": {(DOMAIN, self.config_entry.entry_id)},
            "name": self.config_entry.title,
            "manufacturer": "Boiler Controller",
            "model": "Boiler Controller Module",
            "sw_version": str(version),
        }
