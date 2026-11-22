"""Config flow for read-only Air Bank access."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntry, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.config_entry_oauth2_flow import AbstractOAuth2FlowHandler

from .const import (
    AuthMethod,
    CONF_ACCOUNTS,
    CONF_APPLICATION_ID,
    CONF_ASPS_NAME,
    CONF_AUTH_METHOD,
    CONF_COUNTRY,
    CONF_CERT,
    CONF_FIO_TOKEN,
    CONF_BANK_FOLDERS,
    CONF_IMAP_HOST,
    CONF_ACCOUNT_NAMES,
    CONF_INSTALL_CARDS,
    CONF_LARGE_PAYMENT,
    CONF_LOW_BALANCE,
    CONF_MARK_SEEN,
    CONF_IMAP_PASSWORD,
    CONF_IMAP_PORT,
    CONF_IMAP_USERNAME,
    CONF_KEY,
    CONF_PSU_TYPE,
    CONF_REFRESH_TOKEN,
    CONF_SANDBOX,
    CONF_SESSION_ID,
    CONF_VALID_UNTIL,
    CONF_WEB_API_KEY,
    DOMAIN,
)
from .csas import (
    CeskaSporitelna,
    CeskaSporitelnaError,
    authorization_url,
    bank_api_menu,
    sign_in_menu,
)
from .fio import Fio, FioError, collect_fio_snapshots, fio_unique_id
from .lib.enable_banking import (
    EnableBanking,
    EnableBankingError,
    aspsp_names,
    begin_authorization,
    finish_authorization,
    private_key_ready,
)
from .mail import EmailLoginError, open_mailbox
from .notices import BANK_PARSERS, configured_bank_folders, folder_field, format_account_names, parse_account_names
from .oauth import AirBankOAuth2Implementation, authorization_state, home_assistant_redirect_uri

CSAS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CLIENT_ID): str,
        vol.Required(CONF_CLIENT_SECRET): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
        ),
        vol.Required(CONF_WEB_API_KEY): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
        ),
        vol.Required(CONF_SANDBOX, default=True): bool,
    }
)

FIO_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_FIO_TOKEN): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
        ),
    }
)

LOGGER = logging.getLogger(__name__)

DIRECT_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CLIENT_ID): str,
        vol.Required(CONF_CLIENT_SECRET): str,
        vol.Required(CONF_CERT): str,
        vol.Required(CONF_KEY): str,
        vol.Required(CONF_SANDBOX, default=False): bool,
    }
)
def _required(key: str, default: Any = None) -> vol.Required:
    if default in (None, ""):
        return vol.Required(key)
    return vol.Required(key, default=default)


def _email_schema(defaults: Mapping[str, Any] | None = None) -> vol.Schema:
    values = dict(defaults or {})

    def preset(key: str, fallback: Any) -> Any:
        return values.get(key, fallback)

    fields: dict[Any, Any] = {
        vol.Required(CONF_IMAP_HOST, default=preset(CONF_IMAP_HOST, "imap.gmail.com")): str,
        vol.Required(CONF_IMAP_PORT, default=preset(CONF_IMAP_PORT, 993)): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=65535)
        ),
        _required(CONF_IMAP_USERNAME, values.get(CONF_IMAP_USERNAME)): str,
        vol.Required(CONF_IMAP_PASSWORD): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
        ),
    }
    for parser in BANK_PARSERS:
        field = folder_field(parser.bank_id)
        fields[vol.Required(field, default=preset(field, parser.default_folder))] = str
    fields[vol.Optional(CONF_ACCOUNT_NAMES, default=preset(CONF_ACCOUNT_NAMES, ""))] = selector.TextSelector(
        selector.TextSelectorConfig(multiline=True)
    )
    fields[vol.Required(CONF_MARK_SEEN, default=preset(CONF_MARK_SEEN, True))] = bool
    return vol.Schema(fields)


EMAIL_SCHEMA = _email_schema()
ENABLE_BANKING_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_APPLICATION_ID): str,
        vol.Required(CONF_KEY): str,
        vol.Required(CONF_PSU_TYPE, default="personal"): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=["personal", "business"],
                translation_key="psu_type",
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        ),
        vol.Required(CONF_COUNTRY, default="CZ"): str,
    }
)


def certificate_files_ready(cert_path: str, key_path: str) -> bool:
    """Return whether both PEM files exist."""
    return Path(cert_path).is_file() and Path(key_path).is_file()


class AirBankConfigFlow(AbstractOAuth2FlowHandler, domain=DOMAIN):
    """Let the user choose Enable Banking or a direct Air Bank certificate."""

    DOMAIN = DOMAIN
    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        self._credentials: dict[str, Any] = {}
        self._enable: dict[str, Any] = {}
        self._email: dict[str, Any] = {}
        self._csas: dict[str, Any] = {}
        self._aspsp_names: list[str] = []
        self._enable_state: str | None = None

    @property
    def logger(self) -> logging.Logger:
        return LOGGER

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Let an existing entry set the payment and balance alerts."""
        return AlertOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="user",
            menu_options=list(sign_in_menu()),
            description_placeholders={"redirect_uri": self._redirect_hint()},
        )

    async def async_step_bank_api(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose a bank after the user picks a bank API."""
        return self.async_show_menu(
            step_id="bank_api",
            menu_options=list(bank_api_menu()),
            description_placeholders={"redirect_uri": self._redirect_hint()},
        )

    async def async_step_fio(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Read one Fio account with the holder's own watch token."""
        errors: dict[str, str] = {}
        if user_input is not None:
            token = str(user_input[CONF_FIO_TOKEN]).strip()
            try:
                snapshots = await self.hass.async_add_executor_job(
                    collect_fio_snapshots, Fio(), token, None
                )
            except FioError as err:
                LOGGER.debug("Fio token check failed with status %s", err.status)
                errors["base"] = _fio_form_error(err)
            except OSError:
                errors["base"] = "fio_unavailable"
            else:
                account_id = fio_unique_id(snapshots[0].account_id)
                if self.source == SOURCE_REAUTH:
                    current = self._get_reauth_entry()
                    if current.unique_id != account_id:
                        return self.async_abort(reason="fio_account_mismatch")
                    return self.async_update_reload_and_abort(
                        current,
                        data_updates={CONF_FIO_TOKEN: token},
                    )
                await self.async_set_unique_id(account_id)
                self._abort_if_unique_id_configured()
                return await self._async_create(
                    title="Fio banka",
                    data={CONF_AUTH_METHOD: AuthMethod.FIO, CONF_FIO_TOKEN: token},
                )
        return self.async_show_form(step_id="fio", data_schema=FIO_SCHEMA, errors=errors)

    async def async_step_direct(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if not certificate_files_ready(user_input[CONF_CERT], user_input[CONF_KEY]):
                errors["base"] = "invalid_cert"
            else:
                await self.async_set_unique_id(user_input[CONF_CLIENT_ID])
                self._abort_if_unique_id_configured()
                self._credentials = _direct_credentials(user_input)
                return await self._async_begin_auth()
        return self.async_show_form(
            step_id="direct",
            data_schema=DIRECT_SCHEMA,
            errors=errors,
            description_placeholders={"redirect_uri": self._redirect_hint()},
        )

    async def async_step_enable_banking(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            country = str(user_input.get(CONF_COUNTRY, "")).strip().upper()
            if len(country) != 2 or not country.isalpha():
                errors["base"] = "invalid_country"
            elif not await self.hass.async_add_executor_job(private_key_ready, user_input[CONF_KEY]):
                errors["base"] = "invalid_key"
            else:
                self._enable = _enable_credentials({**user_input, CONF_COUNTRY: country})
                try:
                    self._aspsp_names = await self.hass.async_add_executor_job(self._list_enable_banks)
                except EnableBankingError as err:
                    LOGGER.debug("Enable Banking bank list failed: %s", err)
                    errors["base"] = "invalid_key" if err.status in (401, 403) else "cannot_connect"
                except OSError:
                    errors["base"] = "invalid_key"
                else:
                    if not self._aspsp_names:
                        errors["base"] = "bank_unavailable"
                    else:
                        return await self.async_step_enable_bank()
        return self.async_show_form(
            step_id="enable_banking",
            data_schema=ENABLE_BANKING_SCHEMA,
            errors=errors,
            description_placeholders={"redirect_uri": self._redirect_hint()},
        )

    async def async_step_enable_bank(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._enable[CONF_ASPS_NAME] = user_input[CONF_ASPS_NAME]
            await self.async_set_unique_id(_enable_unique_id(self._enable))
            self._abort_if_unique_id_configured()
            return await self.async_step_enable_consent()
        schema = vol.Schema(
            {
                vol.Required(CONF_ASPS_NAME): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=self._aspsp_names,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )
        return self.async_show_form(
            step_id="enable_bank",
            data_schema=schema,
            description_placeholders={"country": self._enable.get(CONF_COUNTRY, "")},
        )

    async def async_step_enable_consent(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return await self._async_finish_enable_consent(user_input)
        try:
            url = await self.hass.async_add_executor_job(self._start_enable_consent)
        except LookupError:
            return self.async_show_form(
                step_id="enable_banking",
                data_schema=ENABLE_BANKING_SCHEMA,
                errors={"base": "bank_unavailable"},
                description_placeholders={"redirect_uri": self._redirect_hint()},
            )
        except EnableBankingError as err:
            LOGGER.debug("Enable Banking authorization failed: %s", err)
            error = "invalid_key" if err.status in (401, 403) else "cannot_connect"
            return self.async_show_form(
                step_id="enable_banking",
                data_schema=ENABLE_BANKING_SCHEMA,
                errors={"base": error},
                description_placeholders={"redirect_uri": self._redirect_hint()},
            )
        except OSError:
            return self.async_show_form(
                step_id="enable_banking",
                data_schema=ENABLE_BANKING_SCHEMA,
                errors={"base": "invalid_key"},
                description_placeholders={"redirect_uri": self._redirect_hint()},
            )
        return self.async_external_step(step_id="enable_consent", url=url)

    async def async_step_csas(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Collect the user's own Česká spořitelna application."""
        if user_input is not None:
            self._csas = _csas_credentials(user_input)
            await self.async_set_unique_id(_csas_unique_id(self._csas))
            self._abort_if_unique_id_configured()
            return await self.async_step_csas_consent()
        return self.async_show_form(
            step_id="csas",
            data_schema=CSAS_SCHEMA,
            description_placeholders={"redirect_uri": self._redirect_hint()},
        )

    async def async_step_csas_consent(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return await self._async_finish_csas(user_input)
        url = authorization_url(
            sandbox=bool(self._csas.get(CONF_SANDBOX)),
            client_id=self._csas[CONF_CLIENT_ID],
            redirect_uri=self._redirect_hint(),
            state=authorization_state(self.hass, self.flow_id),
        )
        return self.async_external_step(step_id="csas_consent", url=url)

    async def async_step_csas_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(
                step_id="csas_reauth_confirm",
                description_placeholders={"redirect_uri": self._redirect_hint()},
            )
        return await self.async_step_csas_consent()

    async def _async_finish_csas(self, user_input: Mapping[str, Any]) -> ConfigFlowResult:
        if user_input.get("error"):
            reason = "user_rejected_authorize" if user_input["error"] == "access_denied" else "oauth_failed"
            return self.async_abort(reason=reason)
        code = user_input.get("code")
        state = user_input.get("state") or {}
        if not code or state.get("flow_id") != self.flow_id:
            return self.async_abort(reason="oauth_error")
        try:
            refresh_token = await self.hass.async_add_executor_job(self._exchange_csas_code, code)
        except CeskaSporitelnaError as err:
            LOGGER.debug("Česká spořitelna login failed: %s", err)
            reason = "oauth_failed" if err.status in (400, 401, 403) else "cannot_connect"
            return self.async_abort(reason=reason)
        updates = {CONF_REFRESH_TOKEN: refresh_token}
        if self.source == SOURCE_REAUTH:
            return self.async_update_reload_and_abort(self._get_reauth_entry(), data_updates=updates)
        return await self._async_create(title="Česká spořitelna", data={**self._csas, **updates})

    def _exchange_csas_code(self, code: str) -> str:
        bank = CeskaSporitelna(
            sandbox=bool(self._csas.get(CONF_SANDBOX)),
            client_id=self._csas[CONF_CLIENT_ID],
            client_secret=self._csas[CONF_CLIENT_SECRET],
            web_api_key=self._csas[CONF_WEB_API_KEY],
        )
        tokens = bank.exchange_code(code, self._redirect_hint())
        refresh_token = str(tokens.get("refresh_token") or "")
        if not refresh_token:
            raise CeskaSporitelnaError("Česká spořitelna did not return a refresh token")
        bank.accounts(tokens["access_token"])
        return refresh_token

    async def async_step_email(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            credentials = _email_credentials(user_input)
            error = await self._async_open_mailbox(credentials)
            if error is not None:
                return self.async_show_form(
                    step_id="email",
                    data_schema=_email_schema(user_input),
                    errors={"base": error[0]},
                    description_placeholders={"reason": error[1]},
                )
            else:
                await self.async_set_unique_id(credentials[CONF_IMAP_USERNAME].lower())
                self._abort_if_unique_id_configured()
                return await self._async_create(title="Email notices", data=credentials)
        return self.async_show_form(step_id="email", data_schema=EMAIL_SCHEMA, errors=errors)

    async def async_step_email_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            credentials = _email_credentials({**self._email, **user_input})
            error = await self._async_open_mailbox(credentials)
            if error is not None:
                return self.async_show_form(
                    step_id="email_reauth_confirm",
                    data_schema=_email_schema(self._email_suggestions()),
                    errors={"base": error[0]},
                    description_placeholders={"reason": error[1]},
                )
            else:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    data_updates=credentials,
                )
        return self.async_show_form(
            step_id="email_reauth_confirm",
            data_schema=_email_schema(self._email_suggestions()),
            errors=errors,
        )

    def _email_suggestions(self) -> dict[str, Any]:
        return {
            CONF_IMAP_HOST: self._email.get(CONF_IMAP_HOST, "imap.gmail.com"),
            CONF_IMAP_PORT: self._email.get(CONF_IMAP_PORT, 993),
            CONF_IMAP_USERNAME: self._email.get(CONF_IMAP_USERNAME, ""),
            CONF_ACCOUNT_NAMES: format_account_names(self._email.get(CONF_ACCOUNT_NAMES) or {}),
            CONF_MARK_SEEN: bool(self._email.get(CONF_MARK_SEEN, True)),
            **{
                folder_field(bank_id): folder
                for bank_id, folder in configured_bank_folders(self._email).items()
            },
        }

    async def _async_open_mailbox(self, credentials: Mapping[str, Any]) -> tuple[str, str] | None:
        try:
            await self.hass.async_add_executor_job(
                open_mailbox,
                credentials[CONF_IMAP_HOST],
                credentials[CONF_IMAP_PORT],
                credentials[CONF_IMAP_USERNAME],
                credentials[CONF_IMAP_PASSWORD],
                list(credentials[CONF_BANK_FOLDERS].values()),
            )
        except EmailLoginError as err:
            return "invalid_auth", str(err)
        except OSError as err:
            return "imap_failed", str(err)
        return None

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        if entry_data.get(CONF_AUTH_METHOD) == AuthMethod.EMAIL:
            self._email = dict(entry_data)
            return await self.async_step_email_reauth_confirm()
        if entry_data.get(CONF_AUTH_METHOD) == AuthMethod.ENABLE_BANKING:
            self._enable = _enable_credentials(entry_data)
            return await self.async_step_enable_reauth_confirm()
        if entry_data.get(CONF_AUTH_METHOD) == AuthMethod.CSAS:
            self._csas = _csas_credentials(entry_data)
            return await self.async_step_csas_reauth_confirm()
        if entry_data.get(CONF_AUTH_METHOD) == AuthMethod.FIO:
            return await self.async_step_fio()
        self._credentials = _direct_credentials(entry_data)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm",
                description_placeholders={"redirect_uri": self._redirect_hint()},
            )
        return await self._async_begin_auth()

    async def async_step_enable_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(
                step_id="enable_reauth_confirm",
                description_placeholders={"redirect_uri": self._redirect_hint()},
            )
        return await self.async_step_enable_consent()

    async def async_oauth_create_entry(self, data: dict) -> ConfigFlowResult:
        payload = {**self._credentials, **data}
        if self.source == SOURCE_REAUTH:
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(),
                data_updates={"token": data["token"]},
            )
        return await self._async_create(title="Air Bank", data=payload)

    async def _async_begin_auth(self) -> ConfigFlowResult:
        implementation = AirBankOAuth2Implementation(self.hass, self._credentials)
        self.async_register_implementation(self.hass, implementation)
        self.flow_impl = implementation
        return await self.async_step_auth()

    async def _async_finish_enable_consent(self, user_input: Mapping[str, Any]) -> ConfigFlowResult:
        if user_input.get("error"):
            reason = "user_rejected_authorize" if user_input["error"] == "access_denied" else "oauth_failed"
            return self.async_abort(reason=reason)
        code = user_input.get("code")
        state = user_input.get("state") or {}
        if not code or state.get("flow_id") != self.flow_id:
            return self.async_abort(reason="oauth_error")
        try:
            finished = await self.hass.async_add_executor_job(self._exchange_enable_code, code)
        except EnableBankingError as err:
            LOGGER.debug("Enable Banking session exchange failed: %s", err)
            return self.async_abort(reason="oauth_failed")
        except OSError:
            return self.async_abort(reason="oauth_failed")
        updates = {
            CONF_SESSION_ID: finished["session_id"],
            CONF_ACCOUNTS: finished["accounts"],
            CONF_VALID_UNTIL: finished["valid_until"],
        }
        if self.source == SOURCE_REAUTH:
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(),
                data_updates=updates,
            )
        return await self._async_create(
            title=str(self._enable.get(CONF_ASPS_NAME) or "Enable Banking"),
            data={**self._enable, **updates},
        )

    async def _async_create(self, title: str, data: dict[str, Any]) -> ConfigFlowResult:
        """Create the entry. The dashboard is always the modern overview."""
        return self.async_create_entry(title=title, data=data)

    def _start_enable_consent(self) -> str:
        self._enable_state = authorization_state(self.hass, self.flow_id)
        bank = EnableBanking(self._enable[CONF_APPLICATION_ID], self._enable[CONF_KEY])
        return begin_authorization(
            bank,
            aspsp_name=self._enable[CONF_ASPS_NAME],
            country=self._enable[CONF_COUNTRY],
            redirect_url=self._redirect_hint(),
            state=self._enable_state,
            psu_type=self._enable[CONF_PSU_TYPE],
        )

    def _list_enable_banks(self) -> list[str]:
        bank = EnableBanking(self._enable[CONF_APPLICATION_ID], self._enable[CONF_KEY])
        listed = bank.list_aspsps(
            country=self._enable[CONF_COUNTRY],
            psu_type=self._enable[CONF_PSU_TYPE],
            service="AIS",
        )
        return aspsp_names(listed.get("aspsps") or [])

    def _exchange_enable_code(self, code: str) -> dict:
        bank = EnableBanking(self._enable[CONF_APPLICATION_ID], self._enable[CONF_KEY])
        return finish_authorization(bank, code)

    @callback
    def _redirect_hint(self) -> str:
        return home_assistant_redirect_uri(self.hass)


def _direct_credentials(data: Mapping[str, Any]) -> dict[str, Any]:
    return {
        CONF_AUTH_METHOD: AuthMethod.DIRECT,
        CONF_CLIENT_ID: data[CONF_CLIENT_ID],
        CONF_CLIENT_SECRET: data[CONF_CLIENT_SECRET],
        CONF_CERT: data[CONF_CERT],
        CONF_KEY: data[CONF_KEY],
        CONF_SANDBOX: bool(data.get(CONF_SANDBOX, False)),
    }


def _email_credentials(data: Mapping[str, Any]) -> dict[str, Any]:
    return {
        CONF_AUTH_METHOD: AuthMethod.EMAIL,
        CONF_IMAP_HOST: str(data[CONF_IMAP_HOST]).strip(),
        CONF_IMAP_PORT: int(data.get(CONF_IMAP_PORT) or 993),
        CONF_IMAP_USERNAME: str(data[CONF_IMAP_USERNAME]).strip(),
        CONF_IMAP_PASSWORD: data[CONF_IMAP_PASSWORD],
        CONF_BANK_FOLDERS: configured_bank_folders(dict(data)),
        CONF_ACCOUNT_NAMES: parse_account_names(data.get(CONF_ACCOUNT_NAMES) or ""),
        CONF_MARK_SEEN: bool(data.get(CONF_MARK_SEEN, True)),
    }


def _fio_form_error(err: FioError) -> str:
    if err.status == 500:
        return "fio_rejected"
    if err.status == 409:
        return "fio_busy"
    return "fio_unavailable"


def _csas_unique_id(data: Mapping[str, Any]) -> str:
    environment = "sandbox" if data.get(CONF_SANDBOX) else "production"
    return f"{data[CONF_CLIENT_ID]}:{environment}"


def _csas_credentials(data: Mapping[str, Any]) -> dict[str, Any]:
    return {
        CONF_AUTH_METHOD: AuthMethod.CSAS,
        CONF_CLIENT_ID: str(data[CONF_CLIENT_ID]).strip(),
        CONF_CLIENT_SECRET: data[CONF_CLIENT_SECRET],
        CONF_WEB_API_KEY: data[CONF_WEB_API_KEY],
        CONF_SANDBOX: bool(data.get(CONF_SANDBOX, True)),
    }


def _enable_unique_id(data: Mapping[str, Any]) -> str:
    return f"{data[CONF_APPLICATION_ID]}:{data[CONF_COUNTRY]}:{data[CONF_ASPS_NAME]}"


def _enable_credentials(data: Mapping[str, Any]) -> dict[str, Any]:
    country = str(data.get(CONF_COUNTRY) or "CZ").strip().upper()
    return {
        CONF_AUTH_METHOD: AuthMethod.ENABLE_BANKING,
        CONF_APPLICATION_ID: data[CONF_APPLICATION_ID],
        CONF_KEY: data[CONF_KEY],
        CONF_PSU_TYPE: data.get(CONF_PSU_TYPE) or "personal",
        CONF_COUNTRY: country,
        CONF_ASPS_NAME: data.get(CONF_ASPS_NAME) or "Air Bank",
    }


def _alert_schema(current: Mapping[str, Any]) -> vol.Schema:
    fields: dict[Any, Any] = {}
    number = selector.NumberSelector(
        selector.NumberSelectorConfig(min=0, step=0.01, mode=selector.NumberSelectorMode.BOX)
    )
    for key in (CONF_LARGE_PAYMENT, CONF_LOW_BALANCE):
        marker = vol.Optional(key, default=current[key]) if current.get(key) not in (None, "") else vol.Optional(key)
        fields[marker] = number
    fields[vol.Optional(CONF_INSTALL_CARDS, default=current.get(CONF_INSTALL_CARDS, True))] = bool
    return vol.Schema(fields)


class AlertOptionsFlow(OptionsFlow):
    """Optional alerts for a large payment and a low available balance."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Save the two limits. An empty field turns that alert off."""
        if user_input is not None:
            stored = {
                key: user_input[key]
                for key in (CONF_LARGE_PAYMENT, CONF_LOW_BALANCE)
                if user_input.get(key) not in (None, "")
            }
            stored[CONF_INSTALL_CARDS] = bool(user_input.get(CONF_INSTALL_CARDS, True))
            return self.async_create_entry(data=stored)
        return self.async_show_form(step_id="init", data_schema=_alert_schema(self.config_entry.options))


def implementation_from_entry(hass, entry: ConfigEntry) -> AirBankOAuth2Implementation:
    """Build the OAuth implementation stored on a direct Air Bank config entry."""
    return AirBankOAuth2Implementation(hass, entry.data)
