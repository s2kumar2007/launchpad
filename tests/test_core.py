import threading
from runtime.engines import slots, matcher
from runtime.engines.idempotency import IdempotencyStore, SlotStore
def test_free_run():
    m = slots.mask_busy(slots.FULL, 10, 5)
    assert 10 not in slots.free_starts([m], 3) and 15 in slots.free_starts([m], 3)
def test_buffer_widens():
    m = slots.mask_busy(slots.FULL, 20, 1)
    assert 16 not in slots.free_starts([m], 4, buffer_units=2)
def test_matcher():
    assert matcher.match("hair cut", ["Haircut", "Beard Trim"])["status"] == "match"
    assert matcher.match("Meena", ["Mina", "Meena"])["match"] == "Meena"
    assert matcher.match("hair", ["Haircut", "Hair Colour"])["status"] == "ambiguous"
def test_idempotent():
    s, n = IdempotencyStore(), []
    k = s.key("c1", "book", {"a": 1})
    a = s.run(k, lambda: n.append(1) or "ok"); b = s.run(k, lambda: n.append(1) or "x")
    assert a == b == "ok" and len(n) == 1
def test_race_one_winner():
    st, w = SlotStore(), []
    ts = [threading.Thread(target=lambda c=c: st.reserve("s1", c) and w.append(c)) for c in ("a", "b")]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(w) == 1
