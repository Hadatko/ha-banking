from pathlib import Path

from custom_components.ha_bank_watch.config_flow import certificate_files_ready
from custom_components.ha_bank_watch.sensor import SENSORS
from custom_components.ha_bank_watch.snapshot import build_account_snapshot


def test_sensors_match_read_only_account_values():
    snapshot = build_account_snapshot(
        {
            "id": "1",
            "identification": {"iban": "CZ00"},
            "currency": "CZK",
            "nameI18N": "Main",
        },
        [],
        [],
    )
    assert {item.key for item in SENSORS} == set(snapshot.readings())


def test_certificate_files_must_both_exist(tmp_path: Path):
    cert = tmp_path / "client.pem"
    key = tmp_path / "client.key"
    cert.write_text("cert")
    assert certificate_files_ready(str(cert), str(key)) is False
    key.write_text("key")
    assert certificate_files_ready(str(cert), str(key)) is True
