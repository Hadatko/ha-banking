from datetime import date

from custom_components.ha_banking.dashboard import build_dashboard, twelve_month_days


def test_dashboard_lists_balance_graph_and_payments_for_each_account() -> None:
    config = build_dashboard(
        [
            {
                "name": "Savings",
                "sensors": {
                    "available_balance": "sensor.savings_available_balance",
                    "month_incoming": "sensor.savings_month_incoming",
                    "month_outgoing": "sensor.savings_month_outgoing",
                    "month_net": "sensor.savings_month_net",
                    "recent_payments": "sensor.savings_recent_payments",
                },
            }
        ],
        "sk",
        date(2026, 11, 22),
    )
    cards = config["views"][0]["sections"][0]["cards"]
    assert config["views"][0]["title"] == "Banky"
    assert config["views"][0]["theme"] == "HA Banking"
    assert cards[0] == {"type": "heading", "heading": "Savings"}
    assert cards[1]["type"] == "markdown"
    assert "sensor.savings_recent_payments" in cards[1]["content"]
    assert cards[2]["type"] == "statistics-graph"
    assert cards[2]["chart_type"] == "line"
    assert cards[2]["period"] == "day"
    assert cards[2]["days_to_show"] == 366
    assert cards[2]["title"] == "Dostupný zostatok v čase"
    assert cards[2]["entities"][0]["entity"] == "sensor.savings_available_balance"
    assert cards[3]["type"] == "statistics-graph"
    assert cards[3]["title"] == "Dostupný zostatok"
    assert cards[3]["entities"][0]["entity"] == "sensor.savings_available_balance"
    assert cards[4]["chart_type"] == "bar"
    assert cards[4]["period"] == "month"
    assert cards[4]["title"] == "Posledných 12 mesiacov"
    assert cards[4]["days_to_show"] == twelve_month_days(date(2026, 11, 22))
    assert [item["entity"] for item in cards[4]["entities"]] == [
        "sensor.savings_month_incoming",
        "sensor.savings_month_outgoing",
        "sensor.savings_month_net",
    ]
    assert "Príchod" in cards[1]["content"]
    assert "strategy" not in config


def test_twelve_month_window_includes_the_current_month() -> None:
    assert twelve_month_days(date(2026, 11, 22)) == (date(2026, 11, 22) - date(2025, 12, 1)).days
    assert twelve_month_days(date(2026, 1, 15)) == (date(2026, 1, 15) - date(2025, 2, 1)).days


def test_dashboard_without_accounts_is_an_empty_view() -> None:
    config = build_dashboard([], "en")
    assert config["views"][0]["cards"][0]["content"] == "No accounts yet."
