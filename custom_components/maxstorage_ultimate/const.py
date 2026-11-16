DOMAIN = "maxstorage_ultimate"

DEFAULT_USER = "user"
DEFAULT_PASSWORD = "solarmax.com"

CONF_STORAGE_NAME = "maxstorage_name"
CONF_STORAGE_HOST = "maxstorage_host"
CONF_STORAGE_USER = "maxstorage_user"
CONF_STORAGE_PASSWORD = "maxstorage_password"

CONF_SCAN_INTERVAL = "scan_interval"
DEFAULT_SCAN_INTERVAL = 30  # seconds
MIN_SCAN_INTERVAL = 5
MAX_SCAN_INTERVAL = 3600

SENSOR_PREFIX = "MaxStorage"

ERROR_AUTH_INVALID = "invalid_auth"
ERROR_CANNOT_CONNECT = "cannot_connect"
ERROR_UNKNOWN = "unknown"
ERROR_SWARM_WORKER = "swarm_worker"
ERROR_LOGIN_LOCKED = "login_locked"

# Connections to swarm workers (host, user, password, ident) by worker key
CONF_WORKERS = "workers"
