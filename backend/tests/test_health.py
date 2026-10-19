"""Smoke tests for the API entry point."""

from fastapi.testclient import TestClient


def test_health_returns_ok(client: TestClient) -> None:
    """GET /health responds 200 with the expected payload."""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "PAL Accounting Automation API",
    }


def test_health_is_exposed_under_the_api_prefix(client: TestClient) -> None:
    """The same probe is reachable at the versioned prefix."""
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"