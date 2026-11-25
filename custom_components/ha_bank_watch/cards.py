"""Names of the custom cards the HA Banking dashboard needs.

The integration only checks whether a Lovelace resource already points at a card.
Installing button-card, stack-in-card, or mini-graph-card is left to the user.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant


@dataclass(frozen=True)
class Card:
    """A frontend card the dashboard templates refer to."""

    name: str
    repo: str


CARDS = (
    Card("button-card", "custom-cards/button-card"),
    Card("stack-in-card", "custom-cards/stack-in-card"),
    Card("mini-graph-card", "kalkih/mini-graph-card"),
)


def _resources(hass: HomeAssistant) -> Any:
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    lovelace = hass.data.get(LOVELACE_DATA)
    return lovelace.resources if lovelace is not None else None


async def async_missing_cards(hass: HomeAssistant) -> list[str]:
    """Required cards without a Lovelace resource ([] when it cannot be determined)."""
    resources = _resources(hass)
    if resources is None:
        return [card.name for card in CARDS]
    try:
        if not getattr(resources, "loaded", True):
            await resources.async_load()
        urls = " ".join(str(item.get("url", "")) for item in resources.async_items())
    except Exception:  # YAML resources or not loaded
        return []
    return [card.name for card in CARDS if card.name not in urls]
