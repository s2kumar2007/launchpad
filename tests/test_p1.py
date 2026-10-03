import datetime as dt
import json
import os
import zoneinfo
import pytest
from starlette.testclient import TestClient
from runtime.server import app, store, biz
from runtime.config import load
from runtime.store import Store

def test_host_header_allowed(monkeypatch):
    monkeypatch.setenv("ALLOWED_HOSTS", "launchpad.example.com")
    with TestClient(app(), follow_redirects=False) as c:
        # A request with Host: launchpad.example.com should return 200
        r = c.post("/mcp", headers={"Host": "launchpad.example.com", "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})
        assert r.status_code == 200

def test_dev_tokens_rejected_by_default(monkeypatch):
    monkeypatch.delenv("ALLOW_DEV_TOKENS", raising=False)
    with TestClient(app(), follow_redirects=False) as c:
        r = c.post("/mcp", headers={"Authorization": "Bearer dev-alice", "Content-Type": "application/json"}, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_my_bookings", "arguments": {}}})
        assert r.status_code == 401
        assert "www-authenticate" not in r.headers

def test_dev_tokens_accepted_when_enabled(monkeypatch):
    monkeypatch.setenv("ALLOW_DEV_TOKENS", "1")
    with TestClient(app(), follow_redirects=False) as c:
        r = c.post("/mcp", headers={"Authorization": "Bearer dev-alice", "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_my_bookings", "arguments": {}}})
        assert r.status_code == 200

def test_cancel_window_enforced():
    s = Store(load("examples/salon.yaml"))
    tz = zoneinfo.ZoneInfo(s.biz.timezone)
    now = dt.datetime.now(tz)
    # Book 3 days out at 10:00 to ensure slot is available regardless of current time
    d = (now + dt.timedelta(days=3)).date().isoformat()
    res = s._book("alice", "haircut", d, "10:00", "meena")
    assert res["ok"], f"Booking failed: {res}"
    bid = res["booking"]["id"]

    # Directly move the booking to 1 hour from now (within 2-hour cancel window)
    appt_dt = now + dt.timedelta(hours=1)
    s.bookings[bid]["date"] = appt_dt.date().isoformat()
    s.bookings[bid]["time"] = appt_dt.strftime("%H:%M")

    # Attempt to cancel inside window — must be rejected
    cancel_res = s.cancel("alice", bid)
    assert not cancel_res["ok"], f"Cancel should be rejected: {cancel_res}"
    assert cancel_res["error"] == "cancel_window_passed"

    # Verify booking status is still active (BOOKED) and slot was not freed
    assert s.bookings[bid]["status"] == "BOOKED"
    assert s.mine("alice")["bookings"][0]["id"] == bid

def test_date_validation_past_and_horizon(monkeypatch):
    monkeypatch.setenv("ALLOW_DEV_TOKENS", "1")
    with TestClient(app(), follow_redirects=False) as c:
        h = {"Authorization": "Bearer dev-alice", "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        
        # Past date
        past_date = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        r = c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "check_availability", "arguments": {"service": "haircut", "date": past_date, "time": "10:00"}}})
        j = r.json()
        out = j["result"] if "result" in j else {}
        body = out.get("structuredContent") or json.loads(out["content"][0]["text"]) if "content" in out else {}
        assert not body.get("ok") and body.get("error") == "invalid_date"

        # Beyond horizon (> 90 days)
        far_date = (dt.date.today() + dt.timedelta(days=100)).isoformat()
        r2 = c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "check_availability", "arguments": {"service": "haircut", "date": far_date, "time": "10:00"}}})
        j2 = r2.json()
        out2 = j2["result"] if "result" in j2 else {}
        body2 = out2.get("structuredContent") or json.loads(out2["content"][0]["text"]) if "content" in out2 else {}
        assert not body2.get("ok") and body2.get("error") == "date_beyond_horizon"

def test_stats_owner_auth(monkeypatch):
    monkeypatch.setenv("OWNER_TOKEN", "secret-key-123")
    with TestClient(app(), follow_redirects=False) as c:
        # Missing auth
        assert c.get("/api/stats").status_code == 401
        assert "www-authenticate" not in c.get("/api/stats").headers
        
        # Wrong auth
        assert c.get("/api/stats", headers={"Authorization": "Bearer wrong"}).status_code == 401
        
        # Valid auth
        r = c.get("/api/stats", headers={"Authorization": "Bearer secret-key-123"})
        assert r.status_code == 200
        assert "bookings" in r.json()

def test_reschedule_booking_atomic_and_idempotent():
    s = Store(load("examples/salon.yaml"))
    tz = zoneinfo.ZoneInfo(s.biz.timezone)
    now = dt.datetime.now(tz)
    
    # Booking 3 days ahead at 10:00
    d1 = (now + dt.timedelta(days=3)).date().isoformat()
    d2 = (now + dt.timedelta(days=4)).date().isoformat()
    
    b1 = s.book("alice", "haircut", d1, "10:00", "meena")
    assert b1["ok"]
    bid = b1["booking"]["id"]
    
    # Bob takes 11:00 on d2
    s.book("bob", "haircut", d2, "11:00", "meena")
    
    # Reschedule to 11:00 on d2 should fail because it's taken
    fail_res = s.reschedule("alice", bid, d2, "11:00", "meena")
    assert not fail_res["ok"]
    assert fail_res["error"] == "slot_taken"
    # Ensure original booking is untouched
    assert s.bookings[bid]["status"] == "BOOKED"
    assert s.mine("alice")["bookings"][0]["id"] == bid
    
    # Now reschedule to 14:00 on d2 (free)
    ok_res = s.reschedule("alice", bid, d2, "14:00", "meena")
    assert ok_res["ok"]
    new_bid = ok_res["booking"]["id"]
    assert s.bookings[bid]["status"] == "RESCHEDULED"
    assert s.bookings[new_bid]["status"] == "BOOKED"
    
    # Test idempotency: repeat identical call returns identical result
    repeat_res = s.reschedule("alice", bid, d2, "14:00", "meena")
    assert repeat_res == ok_res
