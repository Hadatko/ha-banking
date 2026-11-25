def domestic_payment(
    *,
    instruction_id: str,
    amount: float,
    currency: str,
    debtor_iban: str,
    creditor_iban: str,
    creditor_currency: str | None = None,
    execution_date: str | None = None,
    variable_symbol: str | None = None,
    constant_symbol: str | None = None,
    specific_symbol: str | None = None,
    message: str | None = None,
    instant: bool = False,
    redirect_url: str | None = None,
) -> dict:
    """Build a domestic payment order for PISP version 7.0."""
    references = _symbols(variable_symbol, constant_symbol, specific_symbol)
    return _payment(
        instruction_id=instruction_id,
        amount=amount,
        currency=currency,
        debtor_iban=debtor_iban,
        creditor_iban=creditor_iban,
        creditor_currency=creditor_currency or currency,
        execution_date=execution_date,
        message=message,
        references=references,
        instant=instant,
        redirect_url=redirect_url,
    )


def sepa_payment(
    *,
    instruction_id: str,
    amount: float,
    currency: str,
    debtor_iban: str,
    creditor_iban: str,
    creditor_name: str,
    town_name: str,
    country: str,
    street_name: str | None = None,
    building_number: str | None = None,
    post_code: str | None = None,
    country_subdivision: str | None = None,
    address_lines: list[str] | None = None,
    creditor_bic: str | None = None,
    end_to_end_id: str | None = None,
    execution_date: str | None = None,
    message: str | None = None,
    redirect_url: str | None = None,
) -> dict:
    """Build a SEPA payment order, including the structured address required by version 7.0."""
    payload = _payment(
        instruction_id=instruction_id,
        amount=amount,
        currency=currency,
        debtor_iban=debtor_iban,
        creditor_iban=creditor_iban,
        creditor_currency=None,
        execution_date=execution_date,
        message=message,
        references=[],
        instant=False,
        redirect_url=redirect_url,
    )
    if end_to_end_id:
        payload["paymentIdentification"]["endToEndIdentification"] = end_to_end_id
    address = {"townName": town_name, "country": country}
    if street_name:
        address["streetName"] = street_name
    if building_number:
        address["buildingNumber"] = building_number
    if post_code:
        address["postCode"] = post_code
    if country_subdivision:
        address["countrySubDivision"] = country_subdivision
    if address_lines:
        address["addressLine"] = address_lines
    payload["creditor"] = {"name": creditor_name, "postalAddress": address}
    if creditor_bic:
        payload["creditorAgent"] = {"financialInstitutionIdentification": {"bic": creditor_bic}}
    return payload


def _symbols(
    variable_symbol: str | None,
    constant_symbol: str | None,
    specific_symbol: str | None,
) -> list[str]:
    references = []
    if variable_symbol:
        references.append(f"VS:{variable_symbol}")
    if constant_symbol:
        references.append(f"KS:{constant_symbol}")
    if specific_symbol:
        references.append(f"SS:{specific_symbol}")
    return references


def _payment(
    *,
    instruction_id: str,
    amount: float,
    currency: str,
    debtor_iban: str,
    creditor_iban: str,
    creditor_currency: str | None,
    execution_date: str | None,
    message: str | None,
    references: list[str],
    instant: bool,
    redirect_url: str | None,
) -> dict:
    creditor_account = {"identification": {"iban": creditor_iban}}
    if creditor_currency:
        creditor_account["currency"] = creditor_currency
    payload = {
        "paymentIdentification": {"instructionIdentification": instruction_id},
        "paymentTypeInformation": {"instructionPriority": "INST" if instant else "NORM"},
        "amount": {"instructedAmount": {"value": amount, "currency": currency}},
        "debtorAccount": {"identification": {"iban": debtor_iban}},
        "creditorAccount": creditor_account,
    }
    if execution_date:
        payload["requestedExecutionDate"] = execution_date
    remittance: dict = {}
    if message:
        remittance["unstructured"] = message
    if references:
        remittance["structured"] = {"creditorReferenceInformation": {"reference": references}}
    if remittance:
        payload["remittanceInformation"] = remittance
    if redirect_url:
        payload["redirectUrl"] = redirect_url
    return payload
