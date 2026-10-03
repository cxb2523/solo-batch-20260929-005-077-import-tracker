"""HTTP routes. Both endpoints delegate to the service layer; the panel
is display-only and fetches data from /api/scan."""

# Standard
from pathlib import Path

# Third Party
from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

# Local
from .policy import DEFAULT_DEPTH
from .service import scan_repository

router = APIRouter()

_PANEL_HTML = Path(__file__).with_name("panel.html").read_text(encoding="utf-8")


@router.get("/api/scan")
def api_scan(
    root: str = Query(default="."),
    depth: int = Query(default=DEFAULT_DEPTH, ge=0),
    force: bool = Query(default=False),
):
    # Return the service dict verbatim: the wire contract IS the library
    # contract, same fields in the same order.
    return scan_repository(root, depth, force=force)


@router.get("/panel", response_class=HTMLResponse)
def panel():
    return HTMLResponse(content=_PANEL_HTML)
