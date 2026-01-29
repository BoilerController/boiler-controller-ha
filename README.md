# Boiler Controller HA Integration

Boiler Controller turns your electric boiler into a water battery. Instead of exporting surplus energy, it dumps the excess reported by your P1 meter straight into the heater so you store free solar/dynamic energy as hot water.

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
- A Boiler Controller device configured with the P1 power sensor and Shelly Dimmer

### Calibration (do this before use)

Every dimmer behaves slightly differently and the Shelly power stage reacts differently as it warms up. Run the calibration sweep once before you start relying on the automation so the controller knows how many watts belong to each brightness step.

1. Open the Boiler Controller device page in Home Assistant and press the calibration button (or, if you prefer Services, call `boiler_controller.run_calibration` for your config entry).
2. Let the sweep run from 20% (*) to 100%. The controller will record the wattage for every 1% step and store it as the active profile.
3. If you change hardware or notice large seasonal deviations, rerun the calibration—this profile is the backbone of the calculator (*).

If no calibration exists, the integration falls back to the built-in profile listed in `calculator.py`, but the results are always better with a fresh measurement from your own installation.

\* The power regulator behaves erratically below 20%.
\* A future release will redo the calibration automatically based on detected performance drift.

## Advanced Settings & Manual Override

For ad-hoc control you also get two helper entities once the integration is set up:

- `Select` – **{Integration Name} Dimmer Mode**: choose `auto` to let the controller react to power usage, or `manual` to override the Shelly brightness yourself.
- `Number` – **{Integration Name} Manual Brightness**: specify the brightness percentage (20–100). This value is only applied when the mode select is in `manual`.

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

The controller always works from the calibration profile (either your recorded one or the bundled default curve):

1. **Baseline lookup** – take the current Shelly brightness and read the expected wattage from the profile. If that entry is missing (e.g. Shelly just woke up), fall back to the live Shelly reading.
2. **Add the grid delta** – combine the baseline with the current P1 surplus/deficit (negative grid flow means import). This becomes the target wattage we would like the boiler to draw.
3. **Find the best matching point** – search the calibration profile for the lowest percentage whose wattage can deliver the target value (respecting the hard cap of 2.2 kW, `MAX_EXPORT_WATTS`).
4. **Clamp to allowed range** – enforce the configured min/max dimmer bounds and send that final percentage to the Shelly.

Because the profile already captures how your dimmer responds at each step, this approach automatically compensates for situations where warm hardware performs better than cold hardware.
