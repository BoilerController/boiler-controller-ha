DOMAIN = "boiler_controller"
VERSION = "0.1.0"

PLATFORMS = ["sensor", "select", "number", "button"]

# Configuration flow step IDs
STEP_POWER_SENSOR = "power_sensor"
STEP_SHELLY_CONFIG = "shelly_config"

# Configuration keys
CONF_P1_TOTAL_ENTITY = "power_sensor"  # Renamed to be more generic
CONF_SHELLY_URL = "shelly_url"
CONF_SHELLY_POLL_INTERVAL = "shelly_poll_interval"
CONF_SHELLY_ID = "shelly_id"

# Shelly RPC endpoints
SHELLY_RPC_DEVICE_INFO = "/rpc/Shelly.GetDeviceInfo"
SHELLY_RPC_LIGHT_STATUS = "/rpc/Light.GetStatus"
SHELLY_RPC_LIGHT_SET = "/rpc/Light.Set"
SHELLY_RPC_LIGHT_CONFIG = "/rpc/Light.GetConfig"

# Shelly Dimmer 0/1-10V Gen3 units report either the legacy (0110) hostname prefix
# or the newer "plus" prefix depending on firmware/model. Accept both so discovery
# works for every variant.
SHELLY_DIMMER_HOST_PREFIX = ("shelly0110dimg3-", "shellyplusdimg3-")

# Default settings for the controller
DEFAULT_MIN_DIMMER_VALUE = 0
DEFAULT_MAX_DIMMER_VALUE = 100
# Manual override defaults and modes
DEFAULT_MANUAL_BRIGHTNESS = 0
DIMMER_MODE_AUTO = "auto"
DIMMER_MODE_MANUAL = "manual"
DIMMER_MODES = [DIMMER_MODE_AUTO, DIMMER_MODE_MANUAL]
# Minimum spacing between calculator-driven dimmer updates
DEFAULT_CALCULATOR_MIN_INTERVAL = 15
# Seconds between Shelly status polls
# Where it updates all the sensor values
DEFAULT_SHELLY_POLL_INTERVAL = 15

# Calibration service/options
SERVICE_RUN_CALIBRATION = "run_calibration"
SERVICE_CANCEL_CALIBRATION = "cancel_calibration"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"
CALIBRATION_START_PERCENTAGE = 20
CALIBRATION_END_PERCENTAGE = 100
CALIBRATION_STEP_PERCENTAGE = 1
CALIBRATION_SETTLE_SECONDS = 3
CALIBRATION_STORAGE_VERSION = 1

# Safety limits
MAX_EXPORT_WATTS = 2200