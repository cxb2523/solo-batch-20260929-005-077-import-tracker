"""The HTTP layer must return exactly what the library returns."""

# Standard
import json

# Third Party
from fastapi.testclient import TestClient

# Local
from app.api import app
from app.service import scan_repository


def test_api_matches_library_contract(sample_repo):
    expected = scan_repository(sample_repo, 2)
    client = TestClient(app)
    response = client.get("/api/scan", params={"root": str(sample_repo), "depth": 2})
    assert response.status_code == 200
    payload = response.json()
    # Same fields, same order, same values.
    assert list(payload.keys()) == list(expected.keys())
    assert payload == json.loads(json.dumps(expected))


def test_panel_serves_html():
    client = TestClient(app)
    response = client.get("/panel")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "runScan" in response.text
