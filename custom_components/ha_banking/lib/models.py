from pydantic import BaseModel, ConfigDict, Field, field_validator


class ClientConfig(BaseModel):
    """Connection settings for the production or sandbox Open API."""

    base_url: str = "https://api.airbank.cz"
    sandbox: bool = False
    client_cert: str | None = None
    client_key: str | None = None
    ca_certs: str | None = None
    timeout: float = 30.0

    @field_validator("base_url")
    @classmethod
    def require_https(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError('base_url must start with "https://"')
        return value.rstrip("/")


class RegisteredApplication(BaseModel):
    client_id: str
    client_secret: str
    api_key: str


class Token(BaseModel):
    access_token: str
    expires_in: int
    token_type: str
    refresh_token: str | None = None


class AccountIdentification(BaseModel):
    model_config = ConfigDict(extra="allow")

    iban: str
    other: str | None = None


class Servicer(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    bank_code: str = Field(alias="bankCode")
    country_code: str = Field(alias="countryCode")
    bic: str


class Account(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str
    identification: AccountIdentification
    currency: str
    servicer: Servicer | None = None
    name_i18n: str | None = Field(default=None, alias="nameI18N")
    product_i18n: str | None = Field(default=None, alias="productI18N")
    owners_names: list[str] = Field(default_factory=list, alias="ownersNames")


class AccountPage(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    accounts: list[Account]
    page_number: int | None = Field(default=None, alias="pageNumber")
    page_count: int | None = Field(default=None, alias="pageCount")
    page_size: int | None = Field(default=None, alias="pageSize")
    next_page: int | None = Field(default=None, alias="nextPage")
    total_count: int | None = Field(default=None, alias="totalCount")

    @field_validator(
        "page_number",
        "page_count",
        "page_size",
        "next_page",
        "total_count",
        mode="before",
    )
    @classmethod
    def coerce_paging_number(cls, value: object) -> int | None:
        if value is None or value == "":
            return None
        return int(value)  # type: ignore[arg-type]


class Money(BaseModel):
    model_config = ConfigDict(extra="allow")

    value: float
    currency: str


class Balance(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    type: dict
    credit_line: dict = Field(alias="creditLine")
    amount: Money
    credit_debit_indicator: str = Field(alias="creditDebitIndicator")
    date: dict

    @property
    def type_code(self) -> str:
        return self.type["codeOrProprietary"]["code"]


class Transaction(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    entry_reference: str | None = Field(default=None, alias="entryReference")
    amount: Money
    credit_debit_indicator: str = Field(alias="creditDebitIndicator")
    status: str | None = None


class TransactionPage(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    transactions: list[Transaction] = Field(default_factory=list)
    page_number: int | None = Field(default=None, alias="pageNumber")
    page_count: int | None = Field(default=None, alias="pageCount")
    page_size: int | None = Field(default=None, alias="pageSize")
    next_page: int | None = Field(default=None, alias="nextPage")
    total_count: int | None = Field(default=None, alias="totalCount")

    @field_validator(
        "page_number",
        "page_count",
        "page_size",
        "next_page",
        "total_count",
        mode="before",
    )
    @classmethod
    def coerce_paging_number(cls, value: object) -> int | None:
        if value is None or value == "":
            return None
        return int(value)  # type: ignore[arg-type]


class SignInfo(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    state: str
    sign_id: str | None = Field(default=None, alias="signId")
    sign_info: str | None = Field(default=None, alias="signInfo")


class PaymentInitiation(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    transaction_identification: str = Field(alias="transactionIdentification")
    service_level: dict = Field(alias="serviceLevel")
    sign_info: SignInfo = Field(alias="signInfo")

    @property
    def sign_id(self) -> str | None:
        return self.sign_info.sign_id


class PaymentStatus(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    instruction_status: str = Field(alias="instructionStatus")
