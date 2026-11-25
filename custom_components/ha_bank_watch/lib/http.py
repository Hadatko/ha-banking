from dataclasses import dataclass

import urllib3

from .models import ClientConfig


@dataclass
class HttpResponse:
    status: int
    body: bytes
    headers: dict


class Urllib3Transport:
    """HTTPS transport. Uses the client certificate required outside public APIs."""

    def __init__(self, config: ClientConfig):
        options: dict = {"timeout": urllib3.Timeout(connect=config.timeout, read=config.timeout)}
        if config.client_cert:
            options["cert_file"] = config.client_cert
            if config.client_key:
                options["key_file"] = config.client_key
        if config.ca_certs:
            options["ca_certs"] = config.ca_certs
            options["cert_reqs"] = "CERT_REQUIRED"
        self._http = urllib3.PoolManager(**options)

    def request(self, method: str, url: str, headers: dict | None = None, body: bytes | None = None) -> HttpResponse:
        response = self._http.request(method, url, body=body, headers=headers or {})
        return HttpResponse(response.status, response.data, dict(response.headers))
