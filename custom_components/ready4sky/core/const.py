"""Shared constants for Ready4Sky integration."""

DOMAIN = "ready4sky"
SUPPORTED_DOMAINS = [
    "water_heater",
    "sensor",
    "light",
    "switch",
    "fan",
]

CONF_USE_BACKLIGHT = "use_backlight"
DEFAULT_USE_BACKLIGHT = True
DEFAULT_SCAN_INTERVAL = 60

CONF_MIN_TEMP = 35
CONF_MAX_TEMP = 90

STATUS_OFF = "00"
STATUS_ON = "02"

COOKER_STATUS_PROGRAM = "01"
COOKER_STATUS_KEEP_WARM = "04"
COOKER_STATUS_DELAYED_START = "05"

MODE_BOIL = "00"
MODE_KEEP_WARM = "01"
MODE_LIGHT = "03"

ATTR_WORK_ALLTIME = "Working time (h)"
ATTR_TIMES = "Number starts"
ATTR_SYNC = "Last sync"
ATTR_TIMER_SET = "Timer set"
ATTR_TIMER_CURR = "Timer current"
