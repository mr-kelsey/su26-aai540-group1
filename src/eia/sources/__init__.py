"""Data source plugins.

Each source module defines a class implementing the `Source` Protocol and
registers itself with `registry.register(name, cls)`.
"""

from eia.sources import registry  # noqa: F401  re-export
from eia.sources.base import Source

__all__ = ["Source", "registry"]
