"""Sensor entities for the Boiler Controller integration."""
from __future__ import annotations

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
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

try:
    from homeassistant.const import (
        PERCENTAGE,
        UnitOfPower,
        UnitOfTemperature,
        UnitOfEnergy,
        SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    )
    UNIT_POWER = UnitOfPower.WATT
    UNIT_TEMP = UnitOfTemperature.CELSIUS
    UNIT_ENERGY = UnitOfEnergy.WATT_HOUR
    UNIT_RSSI = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
except ImportError:
    PERCENTAGE = "%"
    UNIT_POWER = "W"
    UNIT_TEMP = "°C"
    UNIT_ENERGY = "Wh"
    UNIT_RSSI = "dBm"

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def _integration_version(controller, config_entry: ConfigEntry) -> str:
    from .const import VERSION as DEFAULT_VERSION
    return str(controller.integration_version or DEFAULT_VERSION)


def _device_info(config_entry: ConfigEntry, controller) -> Dict[str, Any]:
    version = _integration_version(controller, config_entry)
    return {
        "identifiers": {(DOMAIN, config_entry.entry_id)},
        "name": config_entry.title,
        "manufacturer": "Boiler Controller",
        "model": "Boiler Controller Module",
        "sw_version": version,
    }


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Boiler Controller sensor entities."""
    controller = hass.data[DOMAIN][config_entry.entry_id]["controller"]

    entities: List[SensorEntity] = [
        # Main status sensor
        BoilerStatusSensor(hass, config_entry, controller),
        # Live data from /api/status
        BoilerPowerSensor(hass, config_entry, controller),
        BoilerHeatingPercentageSensor(hass, config_entry, controller),
        BoilerTemperatureSensor(hass, config_entry, controller),
        BoilerTotalEnergySensor(hass, config_entry, controller),
        BoilerRssiSensor(hass, config_entry, controller),
        # System info from /api/system
        BoilerFirmwareVersionSensor(hass, config_entry, controller),
        BoilerWifiStrengthSensor(hass, config_entry, controller),
        # Diagnostics
        P1PowerSensor(hass, config_entry, controller),
    ]

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class _BoilerSensorBase(SensorEntity):
    """Base class for sensors that refresh on the status dispatcher signal."""

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
        self._remove_dispatcher: Optional[Callable] = None

    async def async_added_to_hass(self) -> None:
        self._remove_dispatcher = async_dispatcher_connect(
            self.hass,
            self.controller.get_status_signal(),
            self._handle_status_update,
        )
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        if self._remove_dispatcher:
            self._remove_dispatcher()
            self._remove_dispatcher = None

    @callback
    def _handle_status_update(self, payload: dict) -> None:
        self.async_write_ha_state()

    @property
    def device_info(self) -> Dict[str, Any]:
        return _device_info(self.config_entry, self.controller)


# ---------------------------------------------------------------------------
# Main status sensor
# ---------------------------------------------------------------------------


class BoilerStatusSensor(_BoilerSensorBase):
    """High-level status sensor for the Boiler Controller."""

    def __init__(self, hass, config_entry, controller) -> None:
        super().__init__(
            hass,
            config_entry,
            controller,
            name_suffix="Status",
            unique_suffix="status",
            icon="mdi:water-boiler",
        )

    @property
    def state(self) -> str:
        status = self.controller.get_module_status() or {}
        pct = status.get("heatingPercentage")
        if pct is None:
            return "unavailable"
        if pct == 0:
            return "idle"
        return "heating"

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        ctrl = self.controller.get_status()
        status = self.controller.get_module_status() or {}
        system_payload = self.controller.get_module_system() or {}
        system = system_payload.get("system", {}) or {}

        attrs: Dict[str, Any] = {
            "control_mode": ctrl.get("control_mode"),
            "manual_percentage": ctrl.get("manual_percentage"),
            "power_sensor": ctrl.get("power_sensor"),
            "boiler_host": ctrl.get("boiler_host"),
            "poll_interval": f"{ctrl.get('poll_interval')}s",
            "max_boiler_watts": ctrl.get("max_boiler_watts"),
            "last_power_value": ctrl.get("last_power_value"),
            "last_target_percentage": ctrl.get("last_target_pct"),
            "last_available_watts": ctrl.get("last_available_watts"),
            "last_update": ctrl.get("last_update"),
            "integration_version": _integration_version(
                self.controller, self.config_entry
            ),
        }

        if status:
            attrs.update(
                {
                    "power_w": status.get("power"),
                    "heating_percentage": status.get("heatingPercentage"),
                    "temperature_c": status.get("temperature"),
                    "total_wh": status.get("total"),
                    "rssi_dbm": status.get("rssi"),
                }
            )

        if system:
            attrs.update(
                {
                    "firmware_version": system.get("firmwareVersion"),
                    "cpu_frequency": system.get("cpuFrequency"),
                    "module_ip": system.get("ip"),
                    "wifi_strength_dbm": system.get("wifiStrength"),
                }
            )

        return attrs


# ---------------------------------------------------------------------------
# /api/status sensors
# ---------------------------------------------------------------------------


class BoilerPowerSensor(_BoilerSensorBase):
    """Actual power consumption of the boiler (W)."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UNIT_POWER

    def __init__(self, hass, config_entry, controller) -> None:
        super().__init__(
            hass,
            config_entry,
            controller,
            name_suffix="Power",
            unique_suffix="power",
            icon="mdi:lightning-bolt",
        )

    @property
    def native_value(self) -> Optional[float]:
        status = self.controller.get_module_status() or {}
        val = status.get("power")
        return float(val) if val is not None else None


class BoilerHeatingPercentageSensor(_BoilerSensorBase):
    """Current heating percentage (0-100 %)."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, hass, config_entry, controller) -> None:
        super().__init__(
            hass,
            config_entry,
            controller,
            name_suffix="Heating Percentage",
            unique_suffix="heating_percentage",
            icon="mdi:percent",
        )

    @property
    def native_value(self) -> Optional[int]:
        status = self.controller.get_module_status() or {}
        val = status.get("heatingPercentage")
        return int(val) if val is not None else None


class BoilerTemperatureSensor(_BoilerSensorBase):
    """Boiler water temperature (°C)."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UNIT_TEMP

    def __init__(self, hass, config_entry, controller) -> None:
        super().__init__(
            hass,
            config_entry,
            controller,
            name_suffix="Temperature",
            unique_suffix="temperature",
        )

    @property
    def native_value(self) -> Optional[float]:
        status = self.controller.get_module_status() or {}
        val = status.get("temperature")
        return float(val) if val is not None else None


class BoilerTotalEnergySensor(_BoilerSensorBase):
    """Total energy delivered to the boiler (Wh)."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UNIT_ENERGY

    def __init__(self, hass, config_entry, controller) -> None:
        super().__init__(
            hass,
            config_entry,
            controller,
            name_suffix="Total Energy",
            unique_suffix="total_energy",
            icon="mdi:counter",
        )

    @property
    def native_value(self) -> Optional[float]:
        status = self.controller.get_module_status() or {}
        val = status.get("total")
        return float(val) if val is not None else None


class BoilerRssiSensor(_BoilerSensorBase):
    """RSSI of the boiler module's Wi-Fi connection (dBm)."""

    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UNIT_RSSI
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hass, config_entry, controller) -> None:
        super().__init__(
            hass,
            config_entry,
            controller,
            name_suffix="RSSI",
            unique_suffix="rssi",
        )

    @property
    def native_value(self) -> Optional[int]:
        status = self.controller.get_module_status() or {}
        val = status.get("rssi")
        return int(val) if val is not None else None


# ---------------------------------------------------------------------------
# /api/system sensors
# ---------------------------------------------------------------------------


class BoilerFirmwareVersionSensor(_BoilerSensorBase):
    """Firmware version reported by the boiler module."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hass, config_entry, controller) -> None:
        super().__init__(
            hass,
            config_entry,
            controller,
            name_suffix="Firmware Version",
            unique_suffix="firmware_version",
            icon="mdi:chip",
        )

    @property
    def native_value(self) -> Optional[str]:
        system_payload = self.controller.get_module_system() or {}
        system = system_payload.get("system") or {}
        val = system.get("firmwareVersion")
        return str(val) if val is not None else None

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        system_payload = self.controller.get_module_system() or {}
        system = system_payload.get("system") or {}
        return {
            "cpu_frequency": system.get("cpuFrequency"),
            "module_ip": system.get("ip"),
            "current_datetime": system.get("currentDateTime"),
            "up_since": system.get("upSince"),
        }


class BoilerWifiStrengthSensor(_BoilerSensorBase):
    """Wi-Fi signal strength from /api/system (dBm)."""

    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UNIT_RSSI
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hass, config_entry, controller) -> None:
        super().__init__(
            hass,
            config_entry,
            controller,
            name_suffix="WiFi Strength",
            unique_suffix="wifi_strength",
        )

    @property
    def native_value(self) -> Optional[int]:
        system_payload = self.controller.get_module_system() or {}
        system = system_payload.get("system") or {}
        val = system.get("wifiStrength")
        return int(val) if val is not None else None


# ---------------------------------------------------------------------------
# P1 power sensor mirror (diagnostic)
# ---------------------------------------------------------------------------


class P1PowerSensor(SensorEntity):
    """Mirror the configured P1 power sensor for diagnostics."""

    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UNIT_POWER
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry, controller) -> None:
        self.hass = hass
        self.config_entry = config_entry
        self.controller = controller
        self._attr_name = f"{config_entry.title} P1 Power"
        self._attr_unique_id = f"{config_entry.entry_id}_p1_power"
        self._attr_icon = "mdi:transmission-tower"
        self._remove_dispatcher: Optional[Callable] = None

    async def async_added_to_hass(self) -> None:
        self._remove_dispatcher = async_dispatcher_connect(
            self.hass,
            self.controller.get_status_signal(),
            self._handle_update,
        )
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        if self._remove_dispatcher:
            self._remove_dispatcher()
            self._remove_dispatcher = None

    @callback
    def _handle_update(self, _: Any) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> Optional[float]:
        state = self.hass.states.get(self.controller.power_sensor_id)
        if not state or state.state in ("unknown", "unavailable", "none"):
            return None
        try:
            raw = float(state.state)
            unit = str(
                state.attributes.get("unit_of_measurement") or ""
            )
            if unit.strip().lower().startswith("kw"):
                return raw * 1000
            return raw
        except (ValueError, TypeError):
            return None

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        state = self.hass.states.get(self.controller.power_sensor_id)
        if not state:
            return {"status": "missing", "entity_id": self.controller.power_sensor_id}
        return {
            "status": "available",
            "entity_id": self.controller.power_sensor_id,
            "unit": state.attributes.get("unit_of_measurement", ""),
            "last_updated": state.last_updated.isoformat(),
        }

    @property
    def device_info(self) -> Dict[str, Any]:
        return _device_info(self.config_entry, self.controller)
