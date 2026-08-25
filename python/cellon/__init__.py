"""Cellon public Python API.

The distribution is published as ``cellon``. The historical ``cello`` package
remains available as a compatibility namespace so existing applications keep
working while new applications can use ``from cellon import App``.
"""

import importlib
import sys

from cello import *  # noqa: F401,F403
from cello import __all__, __version__

# Keep the supported helper modules importable from the new package name.
for _module in (
    "database",
    "guards",
    "middleware",
    "validation",
    "graphql",
    "grpc",
    "messaging",
    "eventsourcing",
    "cqrs",
    "saga",
    "orm",
):
    try:
        sys.modules[f"{__name__}.{_module}"] = importlib.import_module(f"cello.{_module}")
    except ModuleNotFoundError:
        pass

# The native extension keeps its historical module name to avoid breaking
# wheels and third-party code that imports cello._cello directly.
try:
    sys.modules[f"{__name__}._cello"] = importlib.import_module("cello._cello")
except ModuleNotFoundError:
    pass
