"""Config flow for the Boiler Controller integration."""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    DOMAIN,
    CONF_P1_TOTAL_ENTITY,
    CONF_BOILER_HOST,
    CONF_BOILER_ID,
    CONF_POLL_INTERVAL,
    BC_HOST_PREFIX,
    DEFAULT_POLL_INTERVAL,
)
from .boiler_client import BoilerClient

_LOGGER = logging.getLogger(__name__)


def _find_config_entry_for_device(
    hass, device_id: str | None, *, exclude_entry_id: str | None = None
):
    """Return an existing entry that already manages this boiler controller module."""
    if not device_id:
        return None

    normalized = device_id.strip().lower()
    for entry in hass.config_entries.async_entries(DOMAIN):
        if exclude_entry_id and entry.entry_id == exclude_entry_id:
            continue
        entry_device_id = entry.data.get(CONF_BOILER_ID)
        if entry_device_id and entry_device_id.strip().lower() == normalized:
            return entry
        if entry.unique_id and entry.unique_id.strip().lower() == normalized:
            return entry
    return None


class BoilerControllerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle config flow for Boiler Controller."""

    VERSION = 1

    def __init__(self) -> None:
        self.data: dict = {}

    # ------------------------------------------------------------------
    # Zeroconf auto-discovery
    # ------------------------------------------------------------------

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo):
        """Handle Zeroconf discovery for boiler-controller-<UUID>.local devices."""
        hostname = (discovery_info.hostname or discovery_info.name or "").rstrip(".")
        short_hostname = hostname.split(".")[0].lower()

        if not short_hostname.startswith(BC_HOST_PREFIX):
            return self.async_abort(reason="unsupported_device")

        # Derive unique ID from the UUID part of the hostname
        uuid_part = short_hostname[len(BC_HOST_PREFIX):]
        unique_id = uuid_part or short_hostname

        # Build host (prefer IP address from discovery for reliability)
        ip_address = str(discovery_info.host) if discovery_info.host else None
        boiler_host = ip_address or hostname

        existing = _find_config_entry_for_device(self.hass, unique_id)
        if existing:
            return self.async_abort(reason="already_configured")

        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured(
            updates={CONF_BOILER_HOST: boiler_host}
        )

        self.data[CONF_BOILER_HOST] = boiler_host
        self.data[CONF_BOILER_ID] = unique_id
        self.context["title_placeholders"] = {"device": short_hostname}

        return await self.async_step_user()

    # ------------------------------------------------------------------
    # Manual setup steps
    # ------------------------------------------------------------------

    async def async_step_user(self, user_input=None):
        """Step 1 – integration name."""
        errors: dict = {}

        if user_input is not None:
            self.data.update(user_input)
            return await self.async_step_power_sensor()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required("name", default="Boiler Controller"): str}
            ),
            errors=errors,
        )

    async def async_step_power_sensor(self, user_input=None):
        """Step 2 – select the P1 power sensor entity."""
        errors: dict = {}

        if user_input is not None:
            self.data.update(user_input)
            return await self.async_step_boiler_config()

        power_sensors = await self._get_power_sensors()
        if not power_sensors:
            return self.async_abort(reason="no_power_sensors")

        return self.async_show_form(
            step_id="power_sensor",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_P1_TOTAL_ENTITY): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {"value": k, "label": v}
                                for k, v in power_sensors.items()
                            ],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_boiler_config(self, user_input=None):
        """Step 3 – boiler module host/IP and optional settings."""
        errors: dict = {}

        stored_host = self.data.get(CONF_BOILER_HOST, "")

        if user_input is not None:
            host = user_input.get(CONF_BOILER_HOST, "").strip()

            # Basic validation: must not be empty
            if not host:
                errors[CONF_BOILER_HOST] = "invalid_host"
            else:
                client = BoilerClient(self.hass, host)
                if await client.async_test_connection():
                    device_id = self.data.get(CONF_BOILER_ID) or host

                    existing = _find_config_entry_for_device(self.hass, device_id)
                    if existing:
                        return self.async_abort(reason="already_configured")

                    if self.unique_id is None:
                        await self.async_set_unique_id(device_id)

                    self.data.update(
                        {
                            CONF_BOILER_HOST: host,
                            CONF_BOILER_ID: device_id,
                            CONF_POLL_INTERVAL: DEFAULT_POLL_INTERVAL,
                        }
                    )

                    return self.async_create_entry(
                        title=self.data.get("name", "Boiler Controller"),
                        data=self.data,
                    )
                else:
                    errors[CONF_BOILER_HOST] = "cannot_connect"

            stored_host = host

        return self.async_show_form(
            step_id="boiler_config",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_BOILER_HOST, default=stored_host): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "example_host": "192.168.1.100  or  boiler-controller-abcd1234.local"
            },
        )

    # ------------------------------------------------------------------
    # Options flow
    # ------------------------------------------------------------------

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return BoilerControllerOptionsFlow(config_entry)

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    async def _get_power_sensors(self) -> dict:
        """Return a {entity_id: label} dict of plausible power sensors."""
        sensors: dict = {}
        for entity_id in self.hass.states.async_entity_ids("sensor"):
            state = self.hass.states.get(entity_id)
            if not state:
                continue
            if any(
                kw in entity_id.lower()
                for kw in [
                    "power",
                    "watt",
                    "electricity",
                    "verbruik",
                    "opwek",
                    "net_power",
                    "energy",
                ]
            ):
                try:
                    float(state.state)
                    unit = state.attributes.get("unit_of_measurement", "")
                    if any(u in (unit or "").lower() for u in ["w", "kw", "watt"]):
                        label = state.attributes.get("friendly_name", entity_id)
                        sensors[entity_id] = f"{label} ({entity_id}) [{unit}]"
                except (ValueError, TypeError):
                    pass
        return sensors


class BoilerControllerOptionsFlow(config_entries.OptionsFlow):
    """Options flow to update host, poll interval and max watts."""

    def __init__(self, config_entry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage options."""
        errors: dict = {}
        data = self.config_entry.data
        opts = self.config_entry.options

        if user_input is not None:
            host = user_input.get(CONF_BOILER_HOST, "").strip()
            if not host:
                errors[CONF_BOILER_HOST] = "invalid_host"
            else:
                client = BoilerClient(self.hass, host)
                if await client.async_test_connection():
                    return self.async_create_entry(title="", data=user_input)
                errors[CONF_BOILER_HOST] = "cannot_connect"

        current_host = opts.get(CONF_BOILER_HOST, data.get(CONF_BOILER_HOST, ""))
        current_power_sensor = opts.get(
            CONF_P1_TOTAL_ENTITY, data.get(CONF_P1_TOTAL_ENTITY, "")
        )

        power_sensors = await self._get_power_sensors()

        schema_dict: dict = {}
        if power_sensors:
            schema_dict[
                vol.Optional(CONF_P1_TOTAL_ENTITY, default=current_power_sensor)
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[{"value": k, "label": v} for k, v in power_sensors.items()],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )

        schema_dict.update(
            {
                vol.Required(CONF_BOILER_HOST, default=current_host): str,
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
        )

    async def _get_power_sensors(self) -> dict:
        sensors: dict = {}
        for entity_id in self.hass.states.async_entity_ids("sensor"):
            state = self.hass.states.get(entity_id)
            if not state:
                continue
            if any(
                kw in entity_id.lower()
                for kw in ["power", "watt", "electricity", "verbruik", "opwek", "net_power", "energy"]
            ):
                try:
                    float(state.state)
                    unit = state.attributes.get("unit_of_measurement", "")
                    if any(u in (unit or "").lower() for u in ["w", "kw", "watt"]):
                        label = state.attributes.get("friendly_name", entity_id)
                        sensors[entity_id] = f"{label} ({entity_id}) [{unit}]"
                except (ValueError, TypeError):
                    pass
        return sensors
