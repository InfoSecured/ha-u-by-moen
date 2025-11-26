"""Constants for the U by Moen integration."""

DOMAIN = "u_by_moen"
CONF_EMAIL = "email"
CONF_PASSWORD = "password"

# API Endpoints
API_BASE_URL = "https://www.moen-iot.com"
API_AUTHENTICATE = "/v2/authenticate"
API_CREDENTIALS = "/v3/credentials"
API_SHOWERS = "/v2/showers"
API_SHOWER_DETAIL = "/v5/showers/{}"
API_PUSHER_AUTH = "/v3/pusher-auth"

# Pusher
PUSHER_CHANNEL_PREFIX = "private-"

# Device attributes
ATTR_SERIAL_NUMBER = "serial_number"
ATTR_MODE = "mode"
ATTR_CURRENT_TEMP = "current_temperature"
ATTR_TARGET_TEMP = "target_temperature"
ATTR_MAX_TEMP = "max_temp"
ATTR_ACTIVE_PRESET = "active_preset"
ATTR_OUTLETS = "outlets"
ATTR_PRESETS = "presets"
ATTR_FIRMWARE = "current_firmware_version"
ATTR_BATTERY = "battery_level"

# Modes (actual values from API)
MODE_OFF = "off"
MODE_ADJUSTING = "adjusting"  # Shower is on and heating/cooling
MODE_READY = "ready"  # Shower is on and at target temperature
MODE_PAUSE = "pause"
MODE_PAUSED_BY_PRESET = "paused-by-preset"  # Shower paused at temperature by preset's ready_pauses_water setting

# Update interval
UPDATE_INTERVAL = 30  # seconds

# Icons
ICON_SHOWER = "mdi:shower"
ICON_OUTLET = "mdi:water-pump"
ICON_TEMPERATURE = "mdi:thermometer"
