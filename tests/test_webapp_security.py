"""Web BFF Security, CSRF, Strict Headers, SQL Injection & HITL Endpoint Tests."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from pokesell_agent.database import PokeSellLedgerDB, set_ledger_db_for_testing
from pokesell_agent.web_server import create_web_app

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def secure_client():
    db = PokeSellLedgerDB(db_path=":memory:", seed_demo_data=True)
    set_ledger_db_for_testing(db)
    app = create_web_app()
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        yield client
    set_ledger_db_for_testing(None)


def test_strict_security_headers_and_method_allowlist(secure_client: TestClient) -> None:
    resp = secure_client.get("/")
    assert resp.status_code == 200
    assert "default-src 'self'" in resp.headers["Content-Security-Policy"]
    assert "unsafe-inline" not in resp.headers["Content-Security-Policy"]
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert "camera=()" in resp.headers["Permissions-Policy"]

    # Disallowed HTTP verbs (DELETE / PUT / TRACE) must return 405
    del_resp = secure_client.delete("/api/v1/sales")
    assert del_resp.status_code == 405


def test_csrf_protection_and_session_lifecycle(secure_client: TestClient) -> None:
    boot = secure_client.get("/api/v1/session/bootstrap")
    assert boot.status_code == 200
    csrf_token = boot.json()["csrf_token"]
    assert len(csrf_token) == 64

    # POST without X-CSRF-Token MUST be rejected with 403
    no_csrf = secure_client.post(
        "/api/v1/chat",
        json={"prompt": "How many cards sold?", "stream_id": "STREAM-LIVE-001"},
    )
    assert no_csrf.status_code == 403

    # POST with valid X-CSRF-Token succeeds
    ok_chat = secure_client.post(
        "/api/v1/chat",
        headers={"X-CSRF-Token": csrf_token},
        json={
            "prompt": "Sold SLV-POKEMON-CENTER-GENGAR sleeves to @pokefan99 on whatnot for $19",
            "stream_id": "STREAM-LIVE-001",
        },
    )
    assert ok_chat.status_code == 200
    body = ok_chat.json()
    assert body["status"] == "success"
    assert "BAG-" in body["reply"]

    # Dashboard snapshot returns Cache-Control: no-store
    dash = secure_client.get("/api/v1/dashboard")
    assert dash.status_code == 200
    assert dash.headers["Cache-Control"] == "no-store"
    assert len(dash.json()["open_pull_bags"]) == 1

    # Logout invalidates server-side session
    logout = secure_client.post(
        "/api/v1/session/logout",
        headers={"X-CSRF-Token": csrf_token},
        json={},
    )
    assert logout.status_code == 200
    after_logout = secure_client.get("/api/v1/dashboard")
    assert after_logout.status_code == 401


def test_sql_injection_resilience_and_static_frontend_dom_safety(secure_client: TestClient) -> None:
    boot = secure_client.get("/api/v1/session/bootstrap")
    csrf = boot.json()["csrf_token"]

    # Attempt SQL injection payload in buyer_handle and item_title
    sqli_resp = secure_client.post(
        "/api/v1/sales",
        headers={"X-CSRF-Token": csrf},
        json={
            "stream_id": "STREAM-SQLI-TEST",
            "platform": "whatnot",
            "buyer_handle": "@trainer'; DROP TABLE sales_ledger;--",
            "item_title": "Pikachu'); DELETE FROM inventory_items;--",
            "selling_format": "singles",
            "hammer_price_usd": 12.50,
        },
    )
    assert sqli_resp.status_code == 200
    # Verify tables remain completely intact
    dash = secure_client.get("/api/v1/dashboard").json()
    assert len(dash["inventory"]) >= 7

    # Static verification that frontend JS never assigns to innerHTML/outerHTML or uses localStorage
    js_source = (PROJECT_ROOT / "pokesell_agent" / "static" / "app.js").read_text(encoding="utf-8")
    code_lines = [
        line for line in js_source.splitlines() if not line.strip().startswith(("*", "//", "/*"))
    ]
    joined_code = "\n".join(code_lines)
    assert ".innerHTML" not in joined_code
    assert ".outerHTML" not in joined_code
    assert "insertAdjacentHTML" not in joined_code
    assert "localStorage" not in joined_code
    assert "sessionStorage" not in joined_code
