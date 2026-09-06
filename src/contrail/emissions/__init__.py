"""Emissions provider registry."""

from __future__ import annotations

from contrail.config import lookup_type
from contrail.emissions.base import EmissionsProvider
from contrail.emissions.tim import TIMEmissionsProvider

PROVIDERS: dict[str, type] = {
    TIMEmissionsProvider.id: TIMEmissionsProvider,
}


def get_provider(name: str) -> type:
    """Look up an emissions provider class by name."""
    return lookup_type(PROVIDERS, name, "emissions provider", "providers")


__all__ = ["PROVIDERS", "EmissionsProvider", "TIMEmissionsProvider", "get_provider"]
