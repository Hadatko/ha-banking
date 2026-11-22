"""Constants for the Air Bank integration."""

from datetime import timedelta

DOMAIN = "ha_banking"
CONF_CLIENT_ID = "client_id"
CONF_CLIENT_SECRET = "client_secret"
CONF_CERT = "client_cert"
CONF_KEY = "client_key"
CONF_SANDBOX = "sandbox"
CONF_AUTH_METHOD = "auth_method"
CONF_APPLICATION_ID = "application_id"
CONF_PSU_TYPE = "psu_type"
CONF_COUNTRY = "country"
CONF_ASPS_NAME = "aspsp_name"
CONF_SESSION_ID = "session_id"
CONF_ACCOUNTS = "accounts"
CONF_VALID_UNTIL = "session_valid_until"
AUTH_DIRECT = "direct"
AUTH_ENABLE_BANKING = "enable_banking"
AUTH_EMAIL = "email"
CONF_IMAP_HOST = "imap_host"
CONF_IMAP_PORT = "imap_port"
CONF_IMAP_USERNAME = "imap_username"
CONF_IMAP_PASSWORD = "imap_password"
CONF_IMAP_FOLDER = "imap_folder"
CONF_BANK_FOLDERS = "bank_folders"
CONF_MARK_SEEN = "mark_seen"
CONF_ACCOUNT_NAMES = "account_names"
CONF_LARGE_PAYMENT = "large_payment"
CONF_LOW_BALANCE = "low_balance"
SCAN_INTERVAL = timedelta(minutes=15)

SENSOR_KEYS = (
    "booked_balance",
    "available_balance",
    "incoming_payment",
    "outgoing_payment",
    "transaction_status",
    "year_incoming",
    "year_outgoing",
    "year_net",
    "month_incoming",
    "month_outgoing",
    "month_net",
    "recent_payments",
)
