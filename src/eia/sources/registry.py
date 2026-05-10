"""Tiny in-process registry for source plugins."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from eia.sources.base import Source

_REGISTRY: dict[str, type[Source]] = {}


def register(name: str, cls: type[Source]) -> None:
    if name in _REGISTRY:
        raise ValueError(f"Source already registered: {name}")
    _REGISTRY[name] = cls


def get(name: str) -> type[Source]:
    return _REGISTRY[name]


def all_sources() -> dict[str, type[Source]]:
    return dict(_REGISTRY)
