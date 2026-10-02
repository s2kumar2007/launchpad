import threading
from runtime.config import load
from runtime.store import Store
D = "2026-10-05"
def mk(): return Store(load("examples/salon.yaml"))

def test_idempotent_and_taken():
    s = mk(); a = s.book("alice", "haircut", D, "10:00", "meena"); b = s.book("alice", "haircut", D, "10:00", "meena")
    assert a == b and a["ok"] and len(s.bookings) == 1
    assert s.book("bob", "haircut", D, "10:00", "meena")["error"] == "slot_taken"

def test_isolation():
    s = mk(); bid = s.book("alice", "haircut", D, "10:00", "meena")["booking"]["id"]
    assert s.cancel("bob", bid)["error"] == "not_found" and s.mine("bob")["bookings"] == []

def test_gap_filler_refills_slot():
    s = mk(); bid = s.book("alice", "haircut", D, "10:00", "meena")["booking"]["id"]
    s.join_waitlist("bob", "haircut")
    assert s.cancel("alice", bid)["offered_to_waitlist"]
    assert s.book("carol", "haircut", D, "10:00", "meena")["error"] == "slot_taken"   # held for bob
    oid = s.mine("bob")["offers"][0]["id"]
    assert s.accept_offer("carol", oid)["error"] == "offer_unavailable"
    assert s.accept_offer("bob", oid)["ok"]
    st = s.stats(); assert st["refilled"] == 1 and st["recovered_revenue"] == 400 and st["refill_rate"] == 1.0

def test_expired_offer_frees_slot():
    s = mk(); bid = s.book("alice", "haircut", D, "10:00", "meena")["booking"]["id"]
    s.join_waitlist("bob", "haircut"); s.cancel("alice", bid)
    next(iter(s.offers.values()))["expires"] = 0
    assert any(x["time"] == "10:00" for x in s.availability("haircut", D, "10:00", "meena"))

def test_race_one_winner():
    s, w = mk(), []
    ts = [threading.Thread(target=lambda c=c: s.book(c, "haircut", D, "12:00", "meena")["ok"] and w.append(c)) for c in "ab"]
    [t.start() for t in ts]; [t.join() for t in ts]; assert len(w) == 1

def test_bad_config_rejected():
    import pytest
    from runtime.config import Business
    with pytest.raises(Exception): Business(name="x", type="y", services=[], staff=[{"id": "a", "name": "A", "services": ["nope"]}])
