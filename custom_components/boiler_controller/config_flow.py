import logging
from urllib.parse import urlparse

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    DOMAIN,
    CONF_P1_TOTAL_ENTITY,
    CONF_SHELLY_URL,
    CONF_SHELLY_ID,
    SHELLY_DIMMER_HOST_PREFIX,
)
from .shelly_client import ShellyClient

_LOGGER = logging.getLogger(__name__)


def _find_config_entry_for_device(hass, device_id: str | None, *, exclude_entry_id: str | None = None):
    """Return an existing entry that already manages this Shelly."""
    if not device_id:
        return None

    normalized = device_id.lower()
    for entry in hass.config_entries.async_entries(DOMAIN):
        if exclude_entry_id and entry.entry_id == exclude_entry_id:
            continue

        entry_device_id = entry.data.get(CONF_SHELLY_ID)
        if entry_device_id and entry_device_id.lower() == normalized:
            return entry

        if entry.unique_id and entry.unique_id.lower() == normalized:
            return entry

    return None


class ShellyValidationMixin:
    """Shared helpers for validating Shelly connectivity in flows."""

    def _normalize_url(self, url: str) -> str:
        """Normalize the provided Shelly URL."""
        return url.strip().rstrip('/') if url else url

    async def _test_shelly_connection(self, url: str) -> bool:
        """Test connectivity to the Shelly device."""
        try:
            session = async_get_clientsession(self.hass)
            test_url = f"{url}/rpc/Light.GetStatus?id=0"
            async with session.get(test_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    return True
                _LOGGER.warning("Shelly test call returned status %s", resp.status)
        except aiohttp.ClientError as err:
            _LOGGER.warning("Shelly connection error: %s", err)
        except Exception as err:  # pragma: no cover - defensive logging
            _LOGGER.error("Unexpected Shelly test error: %s", err)
        return False

    @staticmethod
    def _decode_discovery_property(value):
        """Ensure Zeroconf TXT values become plain strings."""
        if value is None:
            return None
        if isinstance(value, bytes):
            try:
                return value.decode("utf-8")
            except UnicodeDecodeError:
                return None
        return str(value)

    @staticmethod
    def _normalize_device_id(device_id: str | None) -> str | None:
        """Normalize device identifiers for easier comparisons."""
        if not device_id:
            return None
        return str(device_id).strip().lower()

    async def _fetch_shelly_device_id(self, url: str) -> str | None:
        """Retrieve the Shelly unique device identifier via RPC."""
        client = ShellyClient(self.hass, url)
        payload = await client.async_get_device_info()
        if not payload:
            _LOGGER.warning("Shelly identity request failed for %s", url)
            return self._derive_device_id_from_url(url)

        device_id = ShellyClient.extract_device_id(payload)
        if device_id:
            return device_id

        fallback_device_id = self._derive_device_id_from_url(url)
        if fallback_device_id:
            _LOGGER.debug(
                "Using %s as fallback Shelly ID for %s", fallback_device_id, url
            )
            return fallback_device_id

        _LOGGER.warning("Unable to extract Shelly ID from payload: %s", payload)
        return None

    @staticmethod
    def _derive_device_id_from_url(url: str) -> str | None:
        """Fallback to a stable identifier derived from the URL host/port."""
        if not url:
            return None

        try:
            parsed = urlparse(url)
        except ValueError:
            return None

        host = parsed.hostname
        if not host:
            return None

        host = host.strip().lower().replace(":", "-")
        if parsed.port:
            host = f"{host}-{parsed.port}"

        return host or None


class BoilerControllerConfigFlow(ShellyValidationMixin, config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 4
    
    def __init__(self):
        self.data = {}

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo):
        """Handle Zeroconf discovery for Shelly dimmers."""
        hostname = discovery_info.hostname or discovery_info.name
        if not hostname:
            return self.async_abort(reason="unsupported_device")

        hostname = hostname.rstrip('.')
        short_hostname = hostname.split('.')[0].lower()
        if not short_hostname.startswith(SHELLY_DIMMER_HOST_PREFIX):
            return self.async_abort(reason="unsupported_device")

        properties = discovery_info.properties or {}
        device_id = self._normalize_device_id(self._decode_discovery_property(properties.get("id")))
        host_property = self._decode_discovery_property(properties.get("host"))
        mdns_host = (host_property or hostname).rstrip('.')
        ip_address = str(discovery_info.host) if discovery_info.host else None

        if mdns_host.startswith("http://") or mdns_host.startswith("https://"):
            shelly_url = self._normalize_url(mdns_host)
        else:
            shelly_url = f"http://{mdns_host}"

        unique_id = device_id or short_hostname

        existing_entry = _find_config_entry_for_device(self.hass, unique_id)
        if existing_entry:
            return self.async_abort(reason="already_configured")

        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured(updates={CONF_SHELLY_URL: shelly_url})

        self.data[CONF_SHELLY_URL] = shelly_url
        self.data[CONF_SHELLY_ID] = unique_id
        self.context["title_placeholders"] = {"device": mdns_host}

        return await self.async_step_user()

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        _LOGGER.debug("Boiler Controller config flow started")
        
        errors = {}

        if user_input is not None:
            # Store user input and proceed to power sensor selection
            self.data.update(user_input)
            return await self.async_step_power_sensor()

        schema = vol.Schema({
            vol.Required("name", default="Boiler Controller"): str,
        })

        return self.async_show_form(
            step_id="user", 
            data_schema=schema, 
            errors=errors
        )

    async def async_step_power_sensor(self, user_input=None):
        """Handle power sensor selection."""
        errors = {}

        if user_input is not None:
            # Store power sensor selection
            self.data.update(user_input)
            return await self.async_step_shelly_config()

        # Get power sensors
        power_sensors = await self._get_power_sensors()
        
        if not power_sensors:
            return self.async_abort(reason="no_power_sensors")

        schema = vol.Schema({
            vol.Required(CONF_P1_TOTAL_ENTITY): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"value": key, "label": value}
                        for key, value in power_sensors.items()
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN
                )
            ),
        })

        return self.async_show_form(
            step_id="power_sensor", 
            data_schema=schema, 
            errors=errors
        )

    async def async_step_shelly_config(self, user_input=None):
        """Handle Shelly connection configuration."""
        errors = {}

        stored_url = self.data.get(CONF_SHELLY_URL)
        default_url = self._normalize_url(stored_url) if stored_url else ""

        if user_input is not None:
            shelly_url = self._normalize_url(user_input.get(CONF_SHELLY_URL, ""))
            
            if not shelly_url.startswith(("http://", "https://")):
                errors[CONF_SHELLY_URL] = "invalid_url"
            else:
                # Test Shelly endpoint connectivity
                if await self._test_shelly_connection(shelly_url):
                    device_id = await self._fetch_shelly_device_id(shelly_url)
                    if not device_id:
                        errors[CONF_SHELLY_URL] = "cannot_identify"
                    else:
                        existing_entry = _find_config_entry_for_device(self.hass, device_id)
                        if existing_entry:
                            return self.async_abort(reason="already_configured")

                        if self.unique_id is None:
                            await self.async_set_unique_id(device_id)

                        self.data.update({
                            CONF_SHELLY_URL: shelly_url,
                            CONF_SHELLY_ID: device_id,
                        })

                        return self.async_create_entry(
                            title=self.data.get("name", "Boiler Controller"), 
                            data=self.data
                        )
                else:
                    errors[CONF_SHELLY_URL] = "cannot_connect"

            default_url = shelly_url

        schema = vol.Schema({
            vol.Required(CONF_SHELLY_URL, default=default_url): str
        })

        return self.async_show_form(
            step_id="shelly_config", 
            data_schema=schema, 
            errors=errors
        )

    async def _get_power_sensors(self):
        """Get list of power sensors."""
        sensors = {}
        
        for entity_id in self.hass.states.async_entity_ids('sensor'):
            state = self.hass.states.get(entity_id)
            if not state:
                continue
                
            # Look for power-related sensors
            if any(keyword in entity_id.lower() for keyword in [
                'power', 'watt', 'electricity', 'current_consumption', 'current_production',
                'energy', 'verbruik', 'opwek', 'net_power'
            ]):
                # Check if it has a numeric state and power-related unit
                try:
                    float(state.state)
                    unit = state.attributes.get('unit_of_measurement', '')
                    if any(u in unit.lower() for u in ['w', 'kw', 'watt']):
                        friendly_name = state.attributes.get('friendly_name', entity_id)
                        sensors[entity_id] = f"{friendly_name} ({entity_id}) [{unit}]"
                except (ValueError, TypeError):
                    continue
        
        return sensors

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return BoilerControllerOptionsFlow(config_entry)


class BoilerControllerOptionsFlow(ShellyValidationMixin, config_entries.OptionsFlow):
    """Handle options flow for Boiler Controller."""

    def __init__(self, config_entry):
        super().__init__()
        self._config_entry = config_entry
        self.data = {}

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        if user_input is not None:
            if user_input.get("change_devices"):
                return await self.async_step_power_sensor()
            else:
                # Only update the advanced settings
                return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional("change_devices", default=False): bool,
                vol.Optional("min_dimmer_value", default=self._config_entry.options.get("min_dimmer_value", 0)): int,
                vol.Optional("max_dimmer_value", default=self._config_entry.options.get("max_dimmer_value", 100)): int,
            })
        )

    async def async_step_power_sensor(self, user_input=None):
        """Handle power sensor change."""
        if user_input is not None:
            self.data.update(user_input)
            return await self.async_step_shelly_config()

        # Get power sensors
        power_sensors = await self._get_power_sensors()
        
        if not power_sensors:
            return self.async_abort(reason="no_power_sensors")

        # Get current selection
        current_power_sensor = self._config_entry.data.get(CONF_P1_TOTAL_ENTITY)

        schema = vol.Schema({
            vol.Required(CONF_P1_TOTAL_ENTITY, default=current_power_sensor): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"value": key, "label": value}
                        for key, value in power_sensors.items()
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN
                )
            ),
        })

        return self.async_show_form(
            step_id="power_sensor", 
            data_schema=schema
        )

    async def async_step_shelly_config(self, user_input=None):
        """Handle Shelly configuration updates."""
        errors = {}

        stored_url = self._config_entry.data.get(CONF_SHELLY_URL)
        current_url = self._normalize_url(stored_url) if stored_url else ""

        if user_input is not None:
            shelly_url = self._normalize_url(user_input.get(CONF_SHELLY_URL, current_url))

            if not shelly_url.startswith(("http://", "https://")):
                errors[CONF_SHELLY_URL] = "invalid_url"
            else:
                device_id = None
                if not await self._test_shelly_connection(shelly_url):
                    errors[CONF_SHELLY_URL] = "cannot_connect"
                else:
                    device_id = await self._fetch_shelly_device_id(shelly_url)
                    if not device_id:
                        errors[CONF_SHELLY_URL] = "cannot_identify"

                if not errors:
                    existing_entry = _find_config_entry_for_device(
                        self.hass,
                        device_id,
                        exclude_entry_id=self._config_entry.entry_id,
                    )
                    if existing_entry:
                        errors[CONF_SHELLY_URL] = "device_in_use"
                    else:
                        # Update stored data/options
                        new_data = dict(self._config_entry.data)
                        if self.data:
                            new_data.update(self.data)
                        new_data[CONF_SHELLY_URL] = shelly_url
                        if device_id:
                            new_data[CONF_SHELLY_ID] = device_id

                        new_options = dict(self._config_entry.options)

                        unique_id = device_id or self._config_entry.unique_id

                        self.hass.config_entries.async_update_entry(
                            self._config_entry,
                            data=new_data,
                            options=new_options,
                            unique_id=unique_id,
                        )

                        await self.hass.config_entries.async_reload(self._config_entry.entry_id)
                        return self.async_create_entry(title="", data={})

            current_url = shelly_url
        schema = vol.Schema({
            vol.Required(CONF_SHELLY_URL, default=current_url): str
        })

        return self.async_show_form(
            step_id="shelly_config",
            data_schema=schema,
            errors=errors,
        )

    async def _get_power_sensors(self):
        """Get list of power sensors."""
        sensors = {}
        
        for entity_id in self.hass.states.async_entity_ids('sensor'):
            state = self.hass.states.get(entity_id)
            if not state:
                continue
                
            # Look for power-related sensors
            if any(keyword in entity_id.lower() for keyword in [
                'power', 'watt', 'electricity', 'current_consumption', 'current_production',
                'energy', 'verbruik', 'opwek', 'net_power'
            ]):
                # Check if it has a numeric state and power-related unit
                try:
                    float(state.state)
                    unit = state.attributes.get('unit_of_measurement', '')
                    if any(u in unit.lower() for u in ['w', 'kw', 'watt']):
                        friendly_name = state.attributes.get('friendly_name', entity_id)
                        sensors[entity_id] = f"{friendly_name} ({entity_id}) [{unit}]"
                except (ValueError, TypeError):
                    continue
        
        return sensors

    async def _get_light_entities(self):
        """Get list of light entities and input_number entities (including dimmers)."""
        entities = {}
        
        # Get light entities (prioritize dimmable lights)
        dimmable_lights = {}
        onoff_lights = {}
        
        for entity_id in self.hass.states.async_entity_ids('light'):
            state = self.hass.states.get(entity_id)
            if not state:
                continue
                
            friendly_name = state.attributes.get('friendly_name', entity_id)
            
            # Check if it supports brightness (dimming)
            supported_features = state.attributes.get('supported_features', 0)
            if supported_features & 1:  # SUPPORT_BRIGHTNESS = 1
                dimmable_lights[entity_id] = f"{friendly_name} ({entity_id}) [Dimmable Light]"
            else:
                onoff_lights[entity_id] = f"{friendly_name} ({entity_id}) [On/Off Light]"
        
        # Get input_number entities (can be used as dimmers)
        input_numbers = {}
        for entity_id in self.hass.states.async_entity_ids('input_number'):
            state = self.hass.states.get(entity_id)
            if not state:
                continue
                
            friendly_name = state.attributes.get('friendly_name', entity_id)
            min_val = state.attributes.get('min', 0)
            max_val = state.attributes.get('max', 100)
            input_numbers[entity_id] = f"{friendly_name} ({entity_id}) [Number: {min_val}-{max_val}]"
        
        # Combine in order: input_numbers, dimmable lights, then on/off lights
        entities.update(input_numbers)
        entities.update(dimmable_lights)
        entities.update(onoff_lights)
        
        return entities
