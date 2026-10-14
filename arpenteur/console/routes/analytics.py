"""Reports: the analytics views, refreshed on demand.

Routes of the analytics module, mounted when it is enabled. Moved out of
`app.py` verbatim: the bodies close over `ctx` where they closed over the
factory's locals, and read `ctx.settings` at request time so a saved
setting reaches the next request.
"""

from __future__ import annotations




from fastapi import Request
from fastapi.responses import (
    HTMLResponse, RedirectResponse,
)

from ...modules.analytics import store as analytics_store
from .. import reports_page as reports_view
from fastapi import APIRouter

from ..context import Console


def router(ctx: Console) -> APIRouter:
    router = APIRouter()
    connect = ctx.connect
    at = ctx.at
    _back = ctx.back

    @router.get("/reports", response_class=HTMLResponse)
    def reports(request: Request) -> HTMLResponse:
        conn = connect()
        try:
            have = analytics_store.available(conn)
            sections, missing = [], []
            for name, title, module in analytics_store.REPORTS:
                if name in have:
                    sections.append((name, title, analytics_store.read(conn, name)))
                else:
                    missing.append((name, module))
            return HTMLResponse(reports_view.reports_page(
                sections, missing=missing,
                fresh=analytics_store.freshness(conn),
                chrome=at("/reports"),
            ))
        finally:
            conn.close()

    @router.post("/reports/refresh")
    def refresh_reports() -> RedirectResponse:
        conn = connect()
        try:
            done = analytics_store.refresh(conn)
        except Exception as exc:
            return _back("/reports", error=f"The refresh failed: {exc}")
        finally:
            conn.close()
        return _back("/reports", note=(
            f"Refreshed {len(done)} view(s): {', '.join(done)}."
            if done else "Nothing materialised to refresh."
        ))

    return router
