from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _distribution_version

try:
    __version__ = _distribution_version("invariant-runtime")
except PackageNotFoundError:
    __version__ = "0+unknown"

__all__ = ["__version__"]
