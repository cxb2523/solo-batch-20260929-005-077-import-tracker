"""
HTTP routing layer: exposes the scanning service over HTTP. All scanning
logic lives in `app.service`; this module only adapts it to HTTP.
"""

# Standard
from pathlib import Path
import json

# Third Party
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse

# Local
from . import service

app = FastAPI(title="import-tracker scan service")

_PANEL_HTML = Path(__file__).with_name("panel.html")


@app.get("/api/scan")
def api_scan(
    root: str = Query(default="."),
    depth: int = Query(default=service.DEFAULT_DEPTH, ge=0),
    force: bool = Query(default=False),
    timeout_ms: int = Query(default=service.DEFAULT_TIMEOUT_MS, ge=1),
):
    """Return the unified scan structure, identical to the library call."""
    try:
        return service.scan_repository(
            root, depth, timeout_ms=timeout_ms, force=force
        )
    except service.ScanTimeoutError as err:
        return JSONResponse(status_code=504, content={"detail": str(err)})
    except FileNotFoundError as err:
        return JSONResponse(status_code=404, content={"detail": str(err)})


@app.get("/panel", response_class=HTMLResponse)
def panel(
    root: str = Query(default="."),
    depth: int = Query(default=service.DEFAULT_DEPTH, ge=0),
):
    """Serve the display-only panel, seeded with an initial scan."""
    try:
        initial = service.scan_repository(root, depth)
        error = None
    except Exception as err:  # the panel must always render
        initial, error = None, str(err)
    html = _PANEL_HTML.read_text(encoding="utf-8")
    html = html.replace("%%INITIAL_DATA%%", _safe_json(initial))
    html = html.replace("%%INITIAL_ERROR%%", _safe_json(error))
    html = html.replace("%%ROOT%%", root).replace("%%DEPTH%%", str(depth))
    return HTMLResponse(html)


def _safe_json(value) -> str:
    """Serialize to JSON that is safe to embed inside a <script> tag."""
    return json.dumps(value).replace("</", "<\\/")
