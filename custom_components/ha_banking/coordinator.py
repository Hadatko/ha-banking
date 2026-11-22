"""Polling for Air Bank account balances and transactions."""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.config_entry_oauth2_flow import OAuth2Session
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    AuthMethod,
    CONF_ACCOUNTS,
    CONF_APPLICATION_ID,
    CONF_ASPS_NAME,
    CONF_AUTH_METHOD,
    CONF_COUNTRY,
    CONF_CERT,
    CONF_ACCOUNT_NAMES,
    CONF_IMAP_HOST,
    CONF_IMAP_PASSWORD,
    CONF_IMAP_PORT,
    CONF_IMAP_USERNAME,
    CONF_MARK_SEEN,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_KEY,
    CONF_REFRESH_TOKEN,
    CONF_SANDBOX,
    CONF_SESSION_ID,
    CONF_WEB_API_KEY,
    DOMAIN,
    SCAN_INTERVAL,
)
from .csas import CeskaSporitelna, CeskaSporitelnaError, collect_csas_snapshots
from .lib.client import AirBank
from .lib.enable_banking import (
    EnableBanking,
    EnableBankingError,
    EnableBankingSessionExpired,
    collect_enable_banking_snapshots,
)
from .lib.errors import AirBankApiError
from .lib.models import ClientConfig
from .mail import EmailLoginError, fetch_air_bank_messages, snapshots_from_messages
from .notices import apply_account_names, configured_bank_folders
from .snapshot import AccountSnapshot, collect_account_snapshots

LOGGER = logging.getLogger(__name__)


class AirBankCoordinator(DataUpdateCoordinator[list[AccountSnapshot]]):
    """Fetch read-only account data."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, session: OAuth2Session | None
    ) -> None:
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.entry = entry
        self.session = session

    async def _async_update_data(self) -> list[AccountSnapshot]:
        method = self.entry.data.get(CONF_AUTH_METHOD)
        if method == AuthMethod.ENABLE_BANKING:
            return await self._async_update_enable_banking()
        if method == AuthMethod.EMAIL:
            return await self._async_update_email()
        if method == AuthMethod.CSAS:
            return await self._async_update_csas()
        if self.session is None:
            raise ConfigEntryAuthFailed("Air Bank login is missing")
        try:
            await self.session.async_ensure_token_valid()
        except ClientError as err:
            if _auth_failure(err):
                raise ConfigEntryAuthFailed("Air Bank login expired") from err
            raise UpdateFailed("Air Bank token refresh failed") from err
        token = self.session.token["access_token"]
        try:
            return await self.hass.async_add_executor_job(self._collect, token)
        except AirBankApiError as err:
            if err.status in (401, 403):
                raise ConfigEntryAuthFailed("Air Bank rejected the access token") from err
            raise UpdateFailed("Air Bank account request failed") from err
        except OSError as err:
            raise UpdateFailed("Air Bank certificate could not be read") from err

    def _collect(self, access_token: str) -> list[AccountSnapshot]:
        data: dict[str, Any] = self.entry.data
        bank = AirBank(
            ClientConfig(
                sandbox=bool(data.get(CONF_SANDBOX)),
                client_cert=data[CONF_CERT],
                client_key=data[CONF_KEY],
            )
        )
        return collect_account_snapshots(bank, access_token)

    async def _async_update_enable_banking(self) -> list[AccountSnapshot]:
        try:
            return await self.hass.async_add_executor_job(self._collect_enable_banking)
        except EnableBankingSessionExpired as err:
            raise ConfigEntryAuthFailed("Enable Banking session expired") from err
        except EnableBankingError as err:
            if err.status in (401, 403):
                raise ConfigEntryAuthFailed("Enable Banking rejected the application key") from err
            raise UpdateFailed("Enable Banking account request failed") from err
        except OSError as err:
            raise UpdateFailed("Enable Banking private key could not be read") from err

    async def _async_update_email(self) -> list[AccountSnapshot]:
        try:
            return await self.hass.async_add_executor_job(self._collect_email)
        except EmailLoginError as err:
            raise ConfigEntryAuthFailed("Air Bank mailbox login failed") from err
        except OSError as err:
            raise UpdateFailed("Air Bank mailbox could not be read") from err

    def _collect_email(self) -> list[AccountSnapshot]:
        data: dict[str, Any] = self.entry.data
        today = dt_util.now().date()
        messages = fetch_air_bank_messages(
            data[CONF_IMAP_HOST],
            int(data.get(CONF_IMAP_PORT) or 993),
            data[CONF_IMAP_USERNAME],
            data[CONF_IMAP_PASSWORD],
            configured_bank_folders(data),
            mark_seen=bool(data.get(CONF_MARK_SEEN, True)),
            today=today,
        )
        snapshots = snapshots_from_messages(messages, today=today)
        return apply_account_names(snapshots, data.get(CONF_ACCOUNT_NAMES) or {})

    async def _async_update_csas(self) -> list[AccountSnapshot]:
        data: dict[str, Any] = self.entry.data
        bank = CeskaSporitelna(
            sandbox=bool(data.get(CONF_SANDBOX)),
            client_id=data[CONF_CLIENT_ID],
            client_secret=data[CONF_CLIENT_SECRET],
            web_api_key=data[CONF_WEB_API_KEY],
        )
        try:
            tokens = await self.hass.async_add_executor_job(bank.refresh, data[CONF_REFRESH_TOKEN])
        except CeskaSporitelnaError as err:
            if err.status in (400, 401, 403):
                raise ConfigEntryAuthFailed("Česká spořitelna login expired") from err
            raise UpdateFailed("Česká spořitelna token refresh failed") from err
        refresh_token = str(tokens.get("refresh_token") or data[CONF_REFRESH_TOKEN])
        if refresh_token != data[CONF_REFRESH_TOKEN]:
            self.hass.config_entries.async_update_entry(
                self.entry, data={**data, CONF_REFRESH_TOKEN: refresh_token}
            )
        try:
            return await self.hass.async_add_executor_job(
                collect_csas_snapshots, bank, tokens["access_token"], dt_util.now().date()
            )
        except CeskaSporitelnaError as err:
            if err.status in (401, 403):
                raise ConfigEntryAuthFailed("Česká spořitelna rejected the access token") from err
            raise UpdateFailed("Česká spořitelna account request failed") from err

    def _collect_enable_banking(self) -> list[AccountSnapshot]:
        data: dict[str, Any] = self.entry.data
        bank = EnableBanking(data[CONF_APPLICATION_ID], data[CONF_KEY])
        return collect_enable_banking_snapshots(
            bank,
            data[CONF_SESSION_ID],
            list(data.get(CONF_ACCOUNTS) or []),
            institution=str(data.get(CONF_ASPS_NAME) or "Air Bank"),
            country=str(data.get(CONF_COUNTRY) or "CZ"),
        )


def _auth_failure(err: ClientError) -> bool:
    message = str(err).lower()
    return any(code in message for code in ("invalid_token", "invalid_grant", "unauthorized"))
