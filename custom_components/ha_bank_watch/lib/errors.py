class AirBankApiError(Exception):
    """Error returned by the Air Bank Open API."""

    def __init__(self, status: int, message: str, codes: list[str], body: object):
        self.status = status
        self.codes = codes
        self.body = body
        super().__init__(f"Air Bank API error {status}: {message}")
