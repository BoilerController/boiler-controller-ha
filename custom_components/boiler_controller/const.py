DOMAIN = "boiler_controller"
VERSION = "2.0.0"

PLATFORMS = ["sensor", "select", "number"]

# Configuration keys
CONF_P1_TOTAL_ENTITY = "power_sensor"
CONF_BOILER_HOST = "boiler_host"
CONF_BOILER_ID = "boiler_id"
CONF_POLL_INTERVAL = "poll_interval"

# mDNS discovery prefix for the boiler controller module
BC_HOST_PREFIX = "boiler-controller-"

# Default settings for the controller

# Default polling interval in seconds for fetching power
# data and updating boiler control sensors
DEFAULT_POLL_INTERVAL = 15
DEFAULT_MAX_BOILER_WATTS = 2200
# Maximum heating percentage step per auto-control cycle (for gradual ramping)
DEFAULT_MAX_STEP_PERCENTAGE = 10
# Minimum interval between auto-control updates (seconds)
# This is a safety measure to prevent too frequent updates in case of rapid grid power fluctuations.
DEFAULT_CONTROLLER_MIN_INTERVAL = 15

# Control modes
CONTROL_MODE_AUTO = "auto"
CONTROL_MODE_MANUAL = "manual"
CONTROL_MODES = [CONTROL_MODE_AUTO, CONTROL_MODE_MANUAL]

# Manual heating percentage bounds
MIN_MANUAL_PERCENTAGE = 0
DEFAULT_MANUAL_PERCENTAGE = 0