import json

from custom_components.ha_banking.dashboard import (
    DETAILS,
    HEAD,
    TEMPLATES,
    build_dashboard,
)

KEYS = (
    "booked_balance",
    "available_balance",
    "transaction_status",
    "year_incoming",
    "year_outgoing",
    "year_net",
    "month_incoming",
    "month_outgoing",
    "month_net",
    "recent_payments",
)


def _account(name: str, keys=KEYS) -> dict:
    slug = name.lower().replace(" ", "_")
    return {"name": name, "sensors": {key: f"sensor.{slug}_{key}" for key in keys}}


def _types(card: dict) -> list[str]:
    return [card["type"], *(t for child in card.get("cards", []) for t in _types(child))]


def test_each_account_is_its_own_sections_view() -> None:
    config = build_dashboard([_account("Savings"), _account("mBank 1")], "cs")
    assert config["button_card_templates"] == TEMPLATES
    assert [view["title"] for view in config["views"]] == ["Přehled", "Savings", "mBank 1"]
    assert [view["path"] for view in config["views"]] == ["overview", "savings", "mbank-1"]
    view = config["views"][1]
    assert view["type"] == "sections"
    assert "theme" not in view
    headings = [section["cards"][0]["heading"] for section in view["sections"]]
    assert headings == [
        "Zůstatek",
        "Tento měsíc",
        "Tento rok",
        "Poslední platby",
        "Dostupný zůstatek v čase",
        "Příjmy a výdaje po měsících",
    ]
    assert "strategy" not in config


def test_sections_use_the_stacked_button_card_layout() -> None:
    view = build_dashboard([_account("Savings")], "en")["views"][1]
    balance = view["sections"][0]["cards"][1]
    assert _types(balance) == [
        "custom:stack-in-card",
        "custom:button-card",
        "custom:mini-graph-card",
        "custom:button-card",
    ]
    head, graph, details = balance["cards"]
    assert head["template"] == HEAD
    assert head["entity"] == "sensor.savings_available_balance"
    assert details["template"] == DETAILS
    assert graph["entities"][0]["entity"] == "sensor.savings_available_balance"
    payments = view["sections"][3]
    assert payments["column_span"] == 2
    assert "sensor.savings_recent_payments" in json.dumps(payments)


def test_history_charts_are_drawn_from_the_months_attribute() -> None:
    view = build_dashboard([_account("Savings")], "sk")["views"][1]
    balance, months = view["sections"][4], view["sections"][5]
    assert balance["cards"][0]["heading"] == "Dostupný zostatok v čase"
    assert months["cards"][0]["heading"] == "Príjmy a výdavky po mesiacoch"
    assert months["column_span"] == 3
    for section in (balance, months):
        text = json.dumps(section)
        assert "statistics-graph" not in text
        rows = section["cards"][1]["cards"][1]["custom_fields"]["rows"]
        assert """at("sensor.savings_recent_payments",'months')""" in rows
        assert _types(section["cards"][1]) == ["custom:stack-in-card", "custom:button-card", "custom:button-card"]


def test_missing_sensors_drop_their_sections() -> None:
    view = build_dashboard([_account("Cash", ("available_balance",))], "en")["views"][1]
    assert [s["cards"][0]["heading"] for s in view["sections"]] == ["Balance"]


def test_same_account_names_get_unique_paths() -> None:
    config = build_dashboard([_account("Main"), _account("Main")], "en")
    assert [view["path"] for view in config["views"]] == ["overview", "main", "main-2"]


def test_an_account_named_overview_does_not_take_the_overview_path() -> None:
    config = build_dashboard([_account("Overview")], "en")
    assert [view["path"] for view in config["views"]] == ["overview", "overview-2"]


def test_overview_lists_every_account_and_sums_them() -> None:
    config = build_dashboard([_account("Savings"), _account("mBank 1")], "cs")
    overview = config["views"][0]
    assert overview["title"] == "Přehled"
    assert [s["cards"][0]["heading"] for s in overview["sections"]] == [
        "Všechny účty",
        "Tento měsíc",
        "Tento rok",
        "Poslední platby",
        "Dostupný zůstatek v čase",
        "Příjmy a výdaje po měsících",
    ]
    text = json.dumps(overview, ensure_ascii=False)
    for entity in ("sensor.savings_available_balance", "sensor.mbank_1_recent_payments", "sensor.mbank_1_year_net"):
        assert entity in text
    assert "mchart(MS,CUR)" in text
    assert "bchart(MS,CUR)" in text
    assert overview["sections"][3]["column_span"] == 2
    assert overview["sections"][5]["column_span"] == 3


def test_counterparty_text_is_escaped() -> None:
    view = build_dashboard([_account("Savings")], "en")["views"][1]
    text = json.dumps(view["sections"][3])
    assert "esc(p.counterparty)" in text
    assert "${p.counterparty}" not in text


def test_bank_supplied_text_is_escaped() -> None:
    config = build_dashboard([_account("Savings")], "en")
    text = json.dumps(config)
    assert "${esc(u)}" in text
    assert "esc(st.state)" in text
    assert "esc(x.currency||'')" in text
    assert "esc(cur(" in text
    assert "unitHtml=esc(unit)" in text
    assert "+(x.currency||'')" not in text


def test_dashboard_without_accounts_is_an_empty_view() -> None:
    config = build_dashboard([], "en")
    assert config["views"][0]["cards"][0]["content"] == "No accounts yet."
