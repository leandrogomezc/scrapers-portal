"""Security checks for protected portal routes."""

import os

os.environ.setdefault("SCRAPE_SECRET", "test-secret-token")

from app import app  # noqa: E402


def test_auth_required_endpoint():
    client = app.test_client()
    res = client.get("/api/auth/required")
    assert res.status_code == 200
    assert res.get_json()["required"] is True


def test_download_without_token_returns_401():
    client = app.test_client()
    res = client.get("/download/beautydepot/csv")
    assert res.status_code == 401


def test_status_without_token_returns_401():
    client = app.test_client()
    res = client.get("/api/solcom/status")
    assert res.status_code == 401


def test_status_with_valid_token():
    client = app.test_client()
    res = client.get(
        "/api/solcom/status",
        headers={"X-Scrape-Token": "test-secret-token"},
    )
    assert res.status_code == 200


def test_run_rejects_token_in_body_only():
    client = app.test_client()
    res = client.post(
        "/api/beautydepot/run",
        json={"token": "test-secret-token"},
    )
    assert res.status_code == 401


def test_security_headers_on_index():
    client = app.test_client()
    res = client.get("/")
    assert res.headers.get("X-Frame-Options") == "DENY"
    assert "default-src 'self'" in res.headers.get("Content-Security-Policy", "")


def test_robots_txt():
    client = app.test_client()
    res = client.get("/robots.txt")
    assert res.status_code == 200
    assert "Disallow: /" in res.get_data(as_text=True)
