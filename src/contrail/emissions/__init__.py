"""Emissions provider registry."""

from __future__ import annotations

from contrail.emissions.base import EmissionsProvider
from contrail.emissions.tim import TIMEmissionsProvider

PROVIDERS: dict[str, type] = {
    TIMEmissionsProvider.id: TIMEmissionsProvider,
}


def get_provider(name: str) -> type:
    """Look up an emissions provider class by name."""
    try:
        return PROVIDERS[name]
    except KeyError:
        available = ", ".join(sorted(PROVIDERS)) or "(none)"
        raise ValueError(
            f"Unknown emissions provider {name!r}. Available providers: {available}"
        ) from None


__all__ = ["PROVIDERS", "EmissionsProvider", "TIMEmissionsProvider", "get_provider"]
