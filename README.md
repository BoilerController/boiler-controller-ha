# Boiler Controller HA Integration

A Home Assistant integration for automatically controlling a Shelly Dimmer 0/1-10V PM Gen3 based on P1 smart meter data.

## Features

This integration:
- Reads data from a P1 smart meter via an existing Home Assistant device
- Controls a Shelly Dimmer 0/1-10V PM Gen3 based on live net consumption
- Automatically switches between different dimmer percentages depending on consumption
- Provides Shelly telemetry sensors (voltage, current, power, temperature, energy)
- Exposes manual override entities so you can switch between automatic logic and a fixed brightness when needed

## Installation

1. Install via HACS or copy the `custom_components/boiler_controller` folder to your Home Assistant configuration
2. Restart Home Assistant
3. Go to Settings > Devices & Services
4. Click "Add Integration" and search for "Boiler Controller"
5. Follow the configuration steps:
   - Select your P1 smart meter device
   - Choose the correct power entity from the P1 meter
   - Select your Shelly Dimmer device

## Configuration

The integration requires:
- A working P1 smart meter integration in Home Assistant
- A Shelly Dimmer 0/1-10V PM Gen3 device connected to Home Assistant

## Advanced Settings & Manual Override

Via the integration options you can adjust the minimum and maximum dimmer bounds that the automatic logic uses.

For ad-hoc control you also get two helper entities once the integration is set up:

- `Select` – **{Integration Name} Dimmer Mode**: choose `auto` to let the controller react to power usage, or `manual` to override the Shelly brightness yourself.
- `Number` – **{Integration Name} Manual Brightness**: specify the brightness percentage (0–100). This value is only applied when the mode select is in `manual`.

Switching back to `auto` immediately returns control to the P1-driven logic.

## Diagnostics & Telemetry

The integration exposes multiple diagnostic sensors in Home Assistant. Besides the Shelly telemetry (voltage, current, power, temperature, energy), you will also see **{Integration Name} Last Dimmer Update**. This timestamp sensor records the last moment the controller actually adjusted the Shelly brightness, whether triggered automatically by the calculator or manually via the override entities. It is not tied to general sensor updates, so its value only changes after a dim command is sent to the Shelly.

| Entity | Type | Description |
| --- | --- | --- |
| **{Integration Name} Status** | Sensor (text) | Shows `Running` when the Shelly dimmer output is ON, `Idle` when it is OFF, and `Error` if Shelly reports an error. Attributes include dimmer bounds, current Shelly metrics, and whether manual mode is active. |
| **{Integration Name} Power Sensor** | Sensor (number, W) | Mirrors the configured P1 entity so you can quickly confirm the source data the controller uses. |
| **{Integration Name} Last Dimmer Update** | Sensor (timestamp) | Timestamp of the last successful Shelly brightness command, regardless of whether it was auto or manual. |
| **{Integration Name} Shelly Brightness / Voltage / Current / Power / Temperature / Energy** | Sensors | Live telemetry polled from the Shelly device. These sensors update whenever the controller’s Shelly poll loop publishes new data. |

## Logic

The default logic:
- At 0W consumption: dimmer at minimum
- At 3000W+ consumption: dimmer at maximum
- In between: linearly scaled between min and max

This logic can be customized in the `controller.py` file.