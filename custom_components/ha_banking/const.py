"""Constants for the Air Bank integration."""

from datetime import timedelta
from enum import StrEnum

DOMAIN = "ha_banking"
CONF_CLIENT_ID = "client_id"
CONF_CLIENT_SECRET = "client_secret"
CONF_WEB_API_KEY = "web_api_key"
CONF_REFRESH_TOKEN = "refresh_token"
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


class AuthMethod(StrEnum):
    """How one config entry reads the bank."""

    DIRECT = "direct"
    ENABLE_BANKING = "enable_banking"
    EMAIL = "email"
    BANK_API = "bank_api"
    CSAS = "csas"


class Direction(StrEnum):
    """Which way money moved."""

    INCOMING = "incoming"
    OUTGOING = "outgoing"


class CreditDebit(StrEnum):
    """ISO credit and debit indicator used by the bank APIs."""

    CREDIT = "CRDT"
    DEBIT = "DBIT"


class BalanceType(StrEnum):
    """ISO balance type codes used by the bank APIs."""

    CLBD = "CLBD"
    ITBD = "ITBD"
    PRCD = "PRCD"
    CLAV = "CLAV"
    ITAV = "ITAV"
    XPCD = "XPCD"
    FWAV = "FWAV"


class DashboardStyle(StrEnum):
    """Look of the generated dashboard."""

    CLASSIC = "classic"
    MODERN = "modern"


DEFAULT_DASHBOARD_STYLE = DashboardStyle.MODERN


class BankId(StrEnum):
    """A mailbox bank the email parser knows."""

    AIRBANK = "airbank"
    CSAS = "csas"
    MBANK = "mbank"


class SensorKey(StrEnum):
    """Sensor and translation key for one account value."""

    BOOKED_BALANCE = "booked_balance"
    AVAILABLE_BALANCE = "available_balance"
    INCOMING_PAYMENT = "incoming_payment"
    OUTGOING_PAYMENT = "outgoing_payment"
    TRANSACTION_STATUS = "transaction_status"
    YEAR_INCOMING = "year_incoming"
    YEAR_OUTGOING = "year_outgoing"
    YEAR_NET = "year_net"
    MONTH_INCOMING = "month_incoming"
    MONTH_OUTGOING = "month_outgoing"
    MONTH_NET = "month_net"
    RECENT_PAYMENTS = "recent_payments"


def direction_from_indicator(indicator: object) -> Direction | None:
    """Map a bank credit or debit code to a payment direction."""
    if indicator == CreditDebit.CREDIT:
        return Direction.INCOMING
    if indicator == CreditDebit.DEBIT:
        return Direction.OUTGOING
    return None
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
CONF_INSTALL_CARDS = "install_cards"
CONF_DASHBOARD_STYLE = "dashboard_style"
SCAN_INTERVAL = timedelta(minutes=15)
BOOKED_BALANCE_TYPES = (BalanceType.CLBD, BalanceType.ITBD, BalanceType.PRCD)
AVAILABLE_BALANCE_TYPES = (BalanceType.CLAV, BalanceType.ITAV, BalanceType.XPCD, BalanceType.FWAV)
