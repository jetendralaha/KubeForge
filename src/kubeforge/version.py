"""Version information, populated at build time or from package metadata."""

from __future__ import annotations

import importlib.metadata

try:
    __version__ = importlib.metadata.version("kubeforge")
except importlib.metadata.PackageNotFoundError:
    __version__ = "0.1.0-dev"


def version_info() -> dict[str, str]:
    import platform
    import sys

    return {
        "version": __version__,
        "python": sys.version,
        "platform": platform.platform(),
    }
