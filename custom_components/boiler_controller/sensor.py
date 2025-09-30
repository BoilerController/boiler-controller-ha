import logging
from typing import Any, Callable, Dict, List, Optional

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

try:
    from homeassistant.const import (
        PERCENTAGE,
        UnitOfElectricCurrent,
        UnitOfElectricPotentialDifference,
        UnitOfEnergy,
        UnitOfPower,
        UnitOfTemperature,
    )

    UNIT_CURRENT = UnitOfElectricCurrent.AMPERE
    UNIT_VOLTAGE = UnitOfElectricPotentialDifference.VOLT
    UNIT_POWER = UnitOfPower.WATT
    UNIT_TEMP = UnitOfTemperature.CELSIUS
    UNIT_ENERGY = UnitOfEnergy.KILO_WATT_HOUR
except ImportError:
    from homeassistant.const import PERCENTAGE

    UNIT_CURRENT = "A"
    UNIT_VOLTAGE = "V"
    UNIT_POWER = "W"
    UNIT_TEMP = "°C"
    UNIT_ENERGY = "kWh"

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def _integration_version(controller, config_entry: ConfigEntry) -> str:
    return controller.integration_version or str(config_entry.version)


def _device_info(config_entry: ConfigEntry, controller) -> Dict[str, Any]:
    """Return standard device info for entities owned by this entry."""
    version = _integration_version(controller, config_entry)
    return {
        "identifiers": {(DOMAIN, config_entry.entry_id)},
        "name": config_entry.title,
        "manufacturer": "Boiler Controller",
        "model": "P1 to Shelly Controller",
        "sw_version": version,
    }


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Boiler Controller sensors."""
    controller_data = hass.data[DOMAIN][config_entry.entry_id]
    controller = controller_data["controller"]

    sensors: List[SensorEntity] = [
        BoilerControllerStatusSensor(hass, config_entry, controller),
        PowerSensorStatusSensor(hass, config_entry, controller),
        LastUpdateSensor(hass, config_entry, controller),
        ShellyBrightnessSensor(hass, config_entry, controller),
        ShellyVoltageSensor(hass, config_entry, controller),
        ShellyCurrentSensor(hass, config_entry, controller),
        ShellyPowerSensor(hass, config_entry, controller),
        ShellyTemperatureSensor(hass, config_entry, controller),
        ShellyEnergySensor(hass, config_entry, controller),
    ]

    async_add_entities(sensors)


class BoilerControllerStatusSensor(SensorEntity):
    """High-level status sensor for the controller."""

    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry, controller) -> None:
        self.hass = hass
        self.config_entry = config_entry
        self.controller = controller
        self._attr_name = f"{config_entry.title} Status"
        self._attr_unique_id = f"{config_entry.entry_id}_status"
        self._attr_icon = "mdi:thermostat"
        self._remove_callbacks: List[Callable] = []

    async def async_added_to_hass(self) -> None:
        self._remove_callbacks.append(
            async_track_state_change_event(
                self.hass,
                [self.controller.power_sensor_id],
                self._handle_power_update,
            )
        )
        self._remove_callbacks.append(
            async_dispatcher_connect(
                self.hass,
                self.controller.get_shelly_status_signal(),
                self._handle_shelly_update,
            )
        )
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        for remove in self._remove_callbacks:
            remove()
        self._remove_callbacks.clear()

    @callback
    def _handle_power_update(self, event) -> None:
        self.async_write_ha_state()

    @callback
    def _handle_shelly_update(self, status) -> None:
        self.async_write_ha_state()

    @property
    def state(self) -> str:
        return "Active" if self.controller._last_update else "Waiting"

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        attrs: Dict[str, Any] = {
            "power_sensor": self.controller.power_sensor_id,
            "shelly_url": self.controller.shelly_url,
            "min_update_interval": f"{self.controller.min_update_interval}s",
            "shelly_poll_interval": f"{self.controller.shelly_poll_interval}s",
            "integration_version": _integration_version(self.controller, self.config_entry),
        }

        controller_status = self.controller.get_status()
        attrs.update(
            {
                "min_dimmer": controller_status.get("min_dimmer"),
                "max_dimmer": controller_status.get("max_dimmer"),
                "device_min_dimmer": controller_status.get("device_min_dimmer"),
                "device_max_dimmer": controller_status.get("device_max_dimmer"),
                "effective_min_dimmer": controller_status.get("effective_min_dimmer"),
                "effective_max_dimmer": controller_status.get("effective_max_dimmer"),
            }
        )

        power_state = self.hass.states.get(self.controller.power_sensor_id)
        if power_state:
            attrs.update(
                {
                    "power_sensor_status": "available",
                    "power_sensor_value": power_state.state,
                    "power_sensor_unit": power_state.attributes.get("unit_of_measurement", "W"),
                }
            )
        else:
            attrs["power_sensor_status"] = "missing"

        status = self.controller.get_shelly_status()
        if status:
            attrs.update(
                {
                    "shelly_source": status.get("source"),
                    "shelly_output": status.get("output"),
                    "shelly_brightness": status.get("brightness"),
                    "shelly_voltage": status.get("voltage"),
                    "shelly_current": status.get("current"),
                    "shelly_power": status.get("apower", status.get("power")),
                }
            )
            temperature = status.get("temperature")
            if isinstance(temperature, dict):
                attrs["shelly_temperature_c"] = temperature.get("tC")
            elif isinstance(temperature, (int, float)):
                attrs["shelly_temperature_c"] = temperature
            energy = status.get("aenergy")
            if isinstance(energy, dict) and isinstance(energy.get("total"), (int, float)):
                attrs["shelly_energy_wh"] = energy["total"]
        else:
            attrs["shelly_status"] = "unavailable"

        if self.controller._last_update:
            attrs["last_update"] = self.controller._last_update.isoformat()
        if self.controller._last_power_value is not None:
            attrs["last_power_value"] = self.controller._last_power_value

        return attrs

    @property
    def device_info(self) -> Dict[str, Any]:
        return _device_info(self.config_entry, self.controller)


class PowerSensorStatusSensor(SensorEntity):
    """Expose the configured power sensor state for debugging."""

    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = "W"

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry, controller) -> None:
        self.hass = hass
        self.config_entry = config_entry
        self.controller = controller
        self._attr_name = f"{config_entry.title} Power Sensor"
        self._attr_unique_id = f"{config_entry.entry_id}_power_sensor"
        self._remove_callbacks: List[Callable] = []

    async def async_added_to_hass(self) -> None:
        self._remove_callbacks.append(
            async_track_state_change_event(
                self.hass,
                [self.controller.power_sensor_id],
                self._handle_power_update,
            )
        )
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        for remove in self._remove_callbacks:
            remove()
        self._remove_callbacks.clear()

    @callback
    def _handle_power_update(self, event) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> Optional[float]:
        power_state = self.hass.states.get(self.controller.power_sensor_id)
        if power_state:
            try:
                value = float(power_state.state)
                unit = self._extract_unit(power_state)
                return self._normalize_power_unit(value, unit)
            except (ValueError, TypeError):
                return None
        return None

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        state = self.hass.states.get(self.controller.power_sensor_id)
        if not state:
            return {"status": "missing"}
        return {
            "status": "available",
            "last_changed": state.last_changed.isoformat(),
            "last_updated": state.last_updated.isoformat(),
            "unit": self._extract_unit(state) or "",
        }

    @staticmethod
    def _extract_unit(state) -> str:
        unit = state.attributes.get("unit_of_measurement")
        if not unit:
            unit = state.attributes.get("native_unit_of_measurement")
        if isinstance(unit, str):
            return unit
        return str(unit) if unit is not None else ""

    @staticmethod
    def _normalize_power_unit(power_value: float, unit: str) -> float:
        if not unit:
            return power_value

        cleaned = unit.strip().lower()
        if cleaned.startswith("kw") or "kilowatt" in cleaned:
            return power_value * 1000
        return power_value

    @property
    def device_info(self) -> Dict[str, Any]:
        return _device_info(self.config_entry, self.controller)


class LastUpdateSensor(SensorEntity):
    """Sensor showing when the controller last updated Shelly."""

    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry, controller) -> None:
        self.hass = hass
        self.config_entry = config_entry
        self.controller = controller
        self._attr_name = f"{config_entry.title} Last Update"
        self._attr_unique_id = f"{config_entry.entry_id}_last_update"
        self._attr_icon = "mdi:clock-outline"
        self._remove_callbacks: List[Callable] = []

    async def async_added_to_hass(self) -> None:
        self._remove_callbacks.append(
            async_track_state_change_event(
                self.hass,
                [self.controller.power_sensor_id],
                self._handle_update,
            )
        )
        self._remove_callbacks.append(
            async_dispatcher_connect(
                self.hass,
                self.controller.get_shelly_status_signal(),
                self._handle_dispatcher_update,
            )
        )
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        for remove in self._remove_callbacks:
            remove()
        self._remove_callbacks.clear()

    @callback
    def _handle_update(self, event) -> None:
        self.async_write_ha_state()

    @callback
    def _handle_dispatcher_update(self, status) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self):
        value = self.controller._last_update
        if isinstance(value, str):
            parsed = dt_util.parse_datetime(value)
            if parsed is not None:
                return parsed
        return value

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        attrs = {
            "min_update_interval": f"{self.controller.min_update_interval}s",
            "update_method": "event_driven",
            "integration_version": _integration_version(self.controller, self.config_entry),
        }
        if self.controller._last_power_value is not None:
            attrs["last_power_value"] = self.controller._last_power_value
        return attrs

    @property
    def device_info(self) -> Dict[str, Any]:
        return _device_info(self.config_entry, self.controller)


class ShellySensorBase(SensorEntity):
    """Base class for Shelly telemetry sensors fed by the controller polling loop."""

    _attr_should_poll = False

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        controller,
        *,
        name_suffix: str,
        unique_suffix: str,
        icon: Optional[str] = None,
    ) -> None:
        self.hass = hass
        self.config_entry = config_entry
        self.controller = controller
        self._attr_name = f"{config_entry.title} {name_suffix}"
        self._attr_unique_id = f"{config_entry.entry_id}_{unique_suffix}"
        self._attr_icon = icon
        self._attr_available = False
        self._attr_native_value: Optional[float] = None
        self._remove_dispatcher: Optional[Callable] = None

    async def async_added_to_hass(self) -> None:
        self._remove_dispatcher = async_dispatcher_connect(
            self.hass,
            self.controller.get_shelly_status_signal(),
            self._handle_shelly_update,
        )
        self._handle_shelly_update(self.controller.get_shelly_status())

    async def async_will_remove_from_hass(self) -> None:
        if self._remove_dispatcher:
            self._remove_dispatcher()
            self._remove_dispatcher = None

    @callback
    def _handle_shelly_update(self, status: Optional[Dict[str, Any]]) -> None:
        if not status:
            if self._attr_available:
                self._attr_available = False
                self._attr_native_value = None
                self._attr_extra_state_attributes = {}
                self.async_write_ha_state()
            return

        self._attr_available = True
        self._attr_native_value = self._extract_value(status)
        self._attr_extra_state_attributes = self._build_extra_state_attributes(status)
        self.async_write_ha_state()

    def _extract_value(self, status: Dict[str, Any]):
        raise NotImplementedError

    def _build_extra_state_attributes(self, status: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "shelly_source": status.get("source"),
            "shelly_output": status.get("output"),
            "errors": status.get("errors"),
        }

    @property
    def device_info(self) -> Dict[str, Any]:
        return _device_info(self.config_entry, self.controller)


class ShellyBrightnessSensor(ShellySensorBase):
    """Expose Shelly brightness (percentage)."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, hass, config_entry, controller) -> None:
        super().__init__(
            hass,
            config_entry,
            controller,
            name_suffix="Shelly Brightness",
            unique_suffix="shelly_brightness",
            icon="mdi:brightness-percent",
        )

    def _extract_value(self, status: Dict[str, Any]) -> Optional[int]:
        brightness = status.get("brightness")
        if isinstance(brightness, (int, float)):
            return int(brightness)
        if status.get("output") is False:
            return 0
        return None


class ShellyVoltageSensor(ShellySensorBase):
    """Expose Shelly reported voltage."""

    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = UNIT_VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, hass, config_entry, controller) -> None:
        super().__init__(
            hass,
            config_entry,
            controller,
            name_suffix="Shelly Voltage",
            unique_suffix="shelly_voltage",
            icon="mdi:sine-wave",
        )

    def _extract_value(self, status: Dict[str, Any]) -> Optional[float]:
        voltage = status.get("voltage")
        if isinstance(voltage, (int, float)):
            return round(voltage, 2)
        return None


class ShellyCurrentSensor(ShellySensorBase):
    """Expose Shelly reported current."""

    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UNIT_CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, hass, config_entry, controller) -> None:
        super().__init__(
            hass,
            config_entry,
            controller,
            name_suffix="Shelly Current",
            unique_suffix="shelly_current",
            icon="mdi:current-ac",
        )

    def _extract_value(self, status: Dict[str, Any]) -> Optional[float]:
        current = status.get("current")
        if isinstance(current, (int, float)):
            return round(current, 3)
        return None


class ShellyPowerSensor(ShellySensorBase):
    """Expose Shelly reported active power."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UNIT_POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, hass, config_entry, controller) -> None:
        super().__init__(
            hass,
            config_entry,
            controller,
            name_suffix="Shelly Power",
            unique_suffix="shelly_power",
            icon="mdi:flash",
        )

    def _extract_value(self, status: Dict[str, Any]) -> Optional[float]:
        power = status.get("apower")
        if power is None:
            power = status.get("power")
        if isinstance(power, (int, float)):
            return round(power, 1)
        return None


class ShellyTemperatureSensor(ShellySensorBase):
    """Expose Shelly internal temperature."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UNIT_TEMP
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, hass, config_entry, controller) -> None:
        super().__init__(
            hass,
            config_entry,
            controller,
            name_suffix="Shelly Temperature",
            unique_suffix="shelly_temperature",
            icon="mdi:thermometer",
        )

    def _extract_value(self, status: Dict[str, Any]) -> Optional[float]:
        temperature = status.get("temperature")
        if isinstance(temperature, dict):
            temperature = temperature.get("tC")
        if isinstance(temperature, (int, float)):
            return round(temperature, 1)
        return None


class ShellyEnergySensor(ShellySensorBase):
    """Expose Shelly cumulative energy in kWh."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UNIT_ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, hass, config_entry, controller) -> None:
        super().__init__(
            hass,
            config_entry,
            controller,
            name_suffix="Shelly Energy",
            unique_suffix="shelly_energy",
            icon="mdi:lightning-bolt",
        )

    def _extract_value(self, status: Dict[str, Any]) -> Optional[float]:
        energy = status.get("aenergy")
        total = None
        if isinstance(energy, dict):
            total = energy.get("total")
        if isinstance(total, (int, float)):
            return round(total / 1000, 3)
        return None

    def _build_extra_state_attributes(self, status: Dict[str, Any]) -> Dict[str, Any]:
        attrs = super()._build_extra_state_attributes(status)
        energy = status.get("aenergy")
        if isinstance(energy, dict) and isinstance(energy.get("total"), (int, float)):
            attrs["shelly_energy_wh"] = energy["total"]
        return attrs