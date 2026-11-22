"""Classic dashboard: the original HA Banking look.

One view with a section per account (entities, a payment list in markdown, statistics-graph
charts) and a theme whose background image follows the light or dark mode. The charts read
the recorder statistics that async_publish_graph_statistics stores on every update.
"""

from __future__ import annotations

import logging
from datetime import date

from homeassistant.core import HomeAssistant

from .const import DOMAIN, Direction, SensorKey

LOGGER = logging.getLogger(__name__)

THEME_NAME = "HA Banking"
_BACKGROUND = {
    "light": "/ha_banking/background-light.jpg",
    "dark": "/ha_banking/background-dark.jpg",
}
_GRAPH_KEYS = (
    SensorKey.AVAILABLE_BALANCE,
    SensorKey.MONTH_INCOMING,
    SensorKey.MONTH_OUTGOING,
    SensorKey.MONTH_NET,
    SensorKey.YEAR_INCOMING,
    SensorKey.YEAR_OUTGOING,
    SensorKey.YEAR_NET,
)
_COPY = {
    "en": {
        "title": "Banks",
        "empty": "No accounts yet.",
        "payments": "Payments",
        "none": "No payments yet.",
        Direction.INCOMING: "Incoming",
        Direction.OUTGOING: "Outgoing",
        "balance": "Available balance",
        "balance_line": "Available balance over time",
        "month": "Last 12 months",
        "year": "This year",
    },
    "sk": {
        "title": "Banky",
        "empty": "Zatiaľ žiadne účty.",
        "payments": "Platby",
        "none": "Zatiaľ žiadne platby.",
        Direction.INCOMING: "Príchod",
        Direction.OUTGOING: "Odchod",
        "balance": "Dostupný zostatok",
        "balance_line": "Dostupný zostatok v čase",
        "month": "Posledných 12 mesiacov",
        "year": "Tento rok",
    },
    "cs": {
        "title": "Banky",
        "empty": "Zatím žádné účty.",
        "payments": "Platby",
        "none": "Zatím žádné platby.",
        Direction.INCOMING: "Příjem",
        Direction.OUTGOING: "Výdaj",
        "balance": "Dostupný zůstatek",
        "balance_line": "Dostupný zůstatek v čase",
        "month": "Posledních 12 měsíců",
        "year": "Tento rok",
    },
}


def twelve_month_days(today: date) -> int:
    """Days back to the first day of the month eleven months ago."""
    index = today.year * 12 + today.month - 1 - 11
    year, month = divmod(index, 12)
    start = date(year, month + 1, 1)
    return (today - start).days


def build_classic_dashboard(accounts: list[dict], language: str, today: date | None = None) -> dict:
    """Lovelace config for the accounts currently registered in Home Assistant."""
    copy = _COPY.get(language[:2].lower(), _COPY["en"])
    today = today or date.today()
    if not accounts:
        return {
            "views": [
                {
                    "title": copy["title"],
                    "theme": THEME_NAME,
                    "cards": [{"type": "markdown", "content": copy["empty"]}],
                }
            ]
        }
    return {
        "views": [
            {
                "title": copy["title"],
                "type": "sections",
                "max_columns": min(len(accounts), 4),
                "theme": THEME_NAME,
                "sections": [_section(account, copy, today) for account in accounts],
            }
        ]
    }


def _section(account: dict, copy: dict, today: date) -> dict:
    sensors = account["sensors"]
    cards: list[dict] = [{"type": "heading", "heading": account["name"]}]
    latest = [
        sensors[key]
        for key in (SensorKey.INCOMING_PAYMENT, SensorKey.OUTGOING_PAYMENT)
        if sensors.get(key)
    ]
    if latest:
        cards.append({"type": "entities", "entities": latest, "title": copy["payments"]})
    recent = sensors.get(SensorKey.RECENT_PAYMENTS)
    if recent:
        cards.append({"type": "markdown", "content": _payment_markdown(recent, copy)})
    available = sensors.get(SensorKey.AVAILABLE_BALANCE)
    if available:
        cards.append(
            _bars([available], copy["balance_line"], days=366, period="day", chart_type="line")
        )
        cards.append(_bars([available], copy["balance"], days=30, period="day"))
    month = [
        sensors[key]
        for key in (SensorKey.MONTH_INCOMING, SensorKey.MONTH_OUTGOING, SensorKey.MONTH_NET)
        if sensors.get(key)
    ]
    if month:
        cards.append(_bars(month, copy["month"], days=twelve_month_days(today), period="month"))
    year = [
        sensors[key]
        for key in (SensorKey.YEAR_INCOMING, SensorKey.YEAR_OUTGOING, SensorKey.YEAR_NET)
        if sensors.get(key)
    ]
    if year:
        cards.append(_bars(year, copy["year"], days=366, period="month"))
    return {"type": "grid", "cards": cards}


def _bars(entities: list[str], title: str, *, days: int, period: str, chart_type: str = "bar") -> dict:
    """Chart of the sensor state. Monetary sensors do not record an average."""
    return {
        "type": "statistics-graph",
        "title": title,
        "chart_type": chart_type,
        "period": period,
        "days_to_show": days,
        "stat_types": ["state"],
        "entities": [{"entity": entity} for entity in entities],
    }


def _payment_markdown(entity_id: str, copy: dict) -> str:
    return (
        "{% set rows = state_attr('" + entity_id + "', 'payments') or [] %}\n"
        "{% if rows | length == 0 %}\n"
        f"{copy['none']}\n"
        "{% else %}\n"
        "{% for row in rows[:20] %}\n"
        "- {{ row.booked or '' }} **{% if row.direction == '" + Direction.INCOMING + "' %}"
        f"{copy['incoming']}"
        "{% else %}"
        f"{copy['outgoing']}"
        "{% endif %}** {{ row.amount }} {{ row.currency }} {{ row.counterparty or '' }}\n"
        "{% endfor %}\n"
        "{% endif %}"
    )


def register_theme(hass: HomeAssistant) -> None:
    """Expose one theme whose background follows the light or dark mode."""
    from homeassistant.const import EVENT_THEMES_UPDATED

    themes = hass.data.setdefault("frontend_themes", {})
    background = "center / cover no-repeat fixed url('{path}')"
    themes[THEME_NAME] = {
        "modes": {
            "light": {"lovelace-background": background.format(path=_BACKGROUND["light"])},
            "dark": {"lovelace-background": background.format(path=_BACKGROUND["dark"])},
        }
    }
    hass.bus.async_fire(EVENT_THEMES_UPDATED)


async def async_register_backgrounds(hass: HomeAssistant, store: dict) -> None:
    if store.get("backgrounds"):
        return
    from pathlib import Path

    from homeassistant.components.http import StaticPathConfig

    folder = Path(__file__).parent
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(url, str(folder / filename), False)
            for url, filename in (
                (_BACKGROUND["light"], "background-light.jpg"),
                (_BACKGROUND["dark"], "background-dark.jpg"),
            )
        ]
    )
    store["backgrounds"] = True


def async_publish_graph_statistics(hass: HomeAssistant, snapshots: list) -> None:
    """Store the current balance and period sums so the bar charts are not empty."""
    from homeassistant.components.recorder.statistics import StatisticMeanType, async_import_statistics
    from homeassistant.helpers import entity_registry as er
    from homeassistant.util import dt as dt_util

    registry = er.async_get(hass)
    start = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    for snapshot in snapshots:
        for key in _GRAPH_KEYS:
            value = getattr(snapshot, key, None)
            if value is None:
                continue
            entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{snapshot.account_id}_{key}")
            if entity_id is None:
                continue
            try:
                async_import_statistics(
                    hass,
                    {
                        "mean_type": StatisticMeanType.NONE,
                        "has_sum": True,
                        "name": None,
                        "source": "recorder",
                        "statistic_id": entity_id,
                        "unit_class": None,
                        "unit_of_measurement": snapshot.currency,
                    },
                    [{"start": start, "state": float(value)}],
                )
            except Exception:
                LOGGER.exception("Could not store a graph point for %s", entity_id)
