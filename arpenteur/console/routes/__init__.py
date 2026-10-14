"""One route file per module, mounted when the module is enabled.

A file here is named after its module and exposes `router(ctx) -> APIRouter`,
and optionally `CONTRIBUTIONS`, the panels it puts into other pages' slots.
`mount` finds each file by name, so a module package never imports the
console: the console looks for the module's routes, not the other way round,
and `tests/test_modules.py` keeps that direction.

Only the enabled modules are looked up. Turning `sales` off in the
configuration removes its routes with its header entries, rather than leaving
pages that answer over a schema that is no longer there.
"""

from __future__ import annotations

import importlib
import importlib.util
from typing import Any

from ..context import Console


def every_route(app: Any) -> list[Any]:
    """Every route the application answers to, included routers expanded.

    FastAPI keeps an included router as one entry of `app.routes` and matches
    inside it on request. A test that walks `app.routes` for the paths that
    can send would miss everything a module mounted, which is the one list
    that must never be incomplete; so the walk goes through here.
    """
    found = []
    for route in app.routes:
        inner = getattr(route, "original_router", None)
        found.extend(inner.routes if inner is not None else [route])
    return found


def mount(app: Any, ctx: Console) -> list[str]:
    """Mount the routes of every enabled module; say which were found."""
    found = []
    for module in ctx.registry.modules:
        name = f"{__name__}.{module.name}"
        if importlib.util.find_spec(name) is None:
            continue
        routes = importlib.import_module(name)
        app.include_router(routes.router(ctx))
        for contribution in getattr(routes, "CONTRIBUTIONS", ()):
            ctx.registry.add(contribution, module=module.name)
        found.append(module.name)
    return found
