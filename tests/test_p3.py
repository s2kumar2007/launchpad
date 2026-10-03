"""P3 tests: multi-tenant routing, /api/businesses, /dashboard, cross-tenant isolation."""
import json, os
import pytest
from starlette.testclient import TestClient
from runtime.server import app

OWNER = "launchpad-owner-secret"

SALON_A = {
    "name": "Alpha Salon",
    "type": "salon",
    "timezone": "UTC",
    "services": [{"id": "cut", "name": "Cut", "duration_min": 30, "price": 200}],
    "staff": [{"id": "bob", "name": "Bob", "services": ["cut"], "start": "09:00", "end": "18:00"}],
    "rules": {"buffer_min": 0, "cancel_window_hours": 2, "hold_offer_minutes": 15},
}
SALON_B = {
    "name": "Beta Clinic",
    "type": "clinic",
    "timezone": "UTC",
    "services": [{"id": "consult", "name": "Consult", "duration_min": 20, "price": 300}],
    "staff": [{"id": "doc", "name": "Dr. Eve", "services": ["consult"], "start": "10:00", "end": "17:00"}],
    "rules": {"buffer_min": 0, "cancel_window_hours": 1, "hold_offer_minutes": 10},
}

def auth_hdr(): return {"Authorization": f"Bearer {OWNER}"}


class TestApiBusinesses:
    def test_missing_auth_returns_401(self):
        with TestClient(app(), follow_redirects=False) as c:
            r = c.post("/api/businesses", json=SALON_A)
            assert r.status_code == 401
            assert "www-authenticate" not in r.headers

    def test_wrong_auth_returns_401(self):
        with TestClient(app(), follow_redirects=False) as c:
            r = c.post("/api/businesses", json=SALON_A, headers={"Authorization": "Bearer wrong"})
            assert r.status_code == 401

    def test_invalid_json_returns_400(self):
        with TestClient(app(), follow_redirects=False) as c:
            r = c.post("/api/businesses", content=b"not json",
                       headers={**auth_hdr(), "Content-Type": "application/json"})
            assert r.status_code == 400

    def test_invalid_config_returns_422(self):
        bad = {**SALON_A, "staff": [{"id": "x", "name": "X", "services": ["nonexistent"],
                                     "start": "09:00", "end": "18:00"}]}
        with TestClient(app(), follow_redirects=False) as c:
            r = c.post("/api/businesses", json=bad, headers=auth_hdr())
            assert r.status_code == 422
            assert "validation_error" in r.json()["error"]

    def test_register_returns_slug_and_mcp_url(self, monkeypatch):
        monkeypatch.setenv("OWNER_TOKEN", OWNER)
        with TestClient(app(), follow_redirects=False) as c:
            r = c.post("/api/businesses", json=SALON_A, headers=auth_hdr())
            assert r.status_code == 201
            d = r.json()
            assert d["ok"]
            assert d["slug"] == "alpha-salon"
            assert d["mcp_url"].endswith("/b/alpha-salon/mcp")
            assert d["business"]["name"] == "Alpha Salon"

    def test_list_businesses_requires_auth(self):
        with TestClient(app(), follow_redirects=False) as c:
            assert c.get("/api/businesses").status_code == 401

    def test_list_businesses_shows_registered(self, monkeypatch):
        monkeypatch.setenv("OWNER_TOKEN", OWNER)
        with TestClient(app(), follow_redirects=False) as c:
            c.post("/api/businesses", json=SALON_A, headers=auth_hdr())
            r = c.get("/api/businesses", headers=auth_hdr())
            assert r.status_code == 200
            slugs = [b["slug"] for b in r.json()["businesses"]]
            assert "alpha-salon" in slugs


class TestDashboard:
    def test_dashboard_serves_html(self):
        with TestClient(app(), follow_redirects=False) as c:
            r = c.get("/dashboard")
            assert r.status_code == 200
            assert "text/html" in r.headers["content-type"]
            assert "Dashboard" in r.text


class TestTenantRouting:
    def test_unknown_tenant_returns_404(self):
        with TestClient(app(), follow_redirects=False) as c:
            r = c.post("/b/no-such-tenant/mcp",
                       headers={"Content-Type": "application/json",
                                "Accept": "application/json, text/event-stream"},
                       json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                             "params": {"protocolVersion": "2025-11-25",
                                        "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})
            assert r.status_code == 404
            assert r.json()["error"] == "tenant_not_found"

    def test_registered_tenant_mcp_reachable(self, monkeypatch):
        monkeypatch.setenv("OWNER_TOKEN", OWNER)
        monkeypatch.setenv("ALLOW_DEV_TOKENS", "1")
        with TestClient(app(), follow_redirects=False) as c:
            # Register
            reg = c.post("/api/businesses", json=SALON_A, headers=auth_hdr())
            assert reg.status_code == 201
            slug = reg.json()["slug"]

            # Call /b/{slug}/mcp initialize
            r = c.post(f"/b/{slug}/mcp",
                       headers={"Content-Type": "application/json",
                                "Accept": "application/json, text/event-stream"},
                       json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                             "params": {"protocolVersion": "2025-11-25",
                                        "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})
            assert r.status_code == 200

    def test_cross_tenant_isolation(self, monkeypatch):
        """Two registered tenants must not share booking state."""
        monkeypatch.setenv("OWNER_TOKEN", OWNER)
        monkeypatch.setenv("ALLOW_DEV_TOKENS", "1")
        with TestClient(app(), follow_redirects=False) as c:
            ra = c.post("/api/businesses", json=SALON_A, headers=auth_hdr())
            rb = c.post("/api/businesses", json=SALON_B, headers=auth_hdr())
            assert ra.status_code == 201
            assert rb.status_code == 201
            slug_a = ra.json()["slug"]
            slug_b = rb.json()["slug"]

            def call_tool(slug, tool, args):
                return c.post(f"/b/{slug}/mcp",
                              headers={"Authorization": "Bearer dev-alice",
                                       "Content-Type": "application/json",
                                       "Accept": "application/json, text/event-stream"},
                              json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                    "params": {"name": tool, "arguments": args}})

            # Tenant A should see "cut" service
            r_a = call_tool(slug_a, "business_info", {})
            assert r_a.status_code == 200
            body_a = r_a.json()
            info_a = body_a.get("result", {}).get("structuredContent") or \
                     json.loads(body_a.get("result", {}).get("content", [{}])[0].get("text", "{}"))
            assert any(s["id"] == "cut" for s in info_a.get("services", []))

            # Tenant B should see "consult" service but NOT "cut"
            r_b = call_tool(slug_b, "business_info", {})
            assert r_b.status_code == 200
            body_b = r_b.json()
            info_b = body_b.get("result", {}).get("structuredContent") or \
                     json.loads(body_b.get("result", {}).get("content", [{}])[0].get("text", "{}"))
            assert any(s["id"] == "consult" for s in info_b.get("services", []))
            assert not any(s["id"] == "cut" for s in info_b.get("services", []))
