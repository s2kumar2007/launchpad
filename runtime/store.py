import itertools, threading, time
from .engines import slots
from .engines.idempotency import IdempotencyStore

def to_units(hhmm):
    h, m = map(int, hhmm.split(":")); return h * 12 + m // 5
def ceil5(x): return -(-x // 5)

class Store:
    """In-memory store. Same interface will be backed by DynamoDB (conditional writes) on deploy."""
    def __init__(self, biz):
        self.biz, self.lock = biz, threading.RLock()
        self.masks, self.bookings, self.waitlist, self.offers, self.events = {}, {}, [], {}, []
        self.idem, self.ids = IdempotencyStore(), itertools.count(1)

    def svc(self, sid): return next((s for s in self.biz.services if s.id == sid), None)
    def staff_for(self, sid, staff_id=None):
        return [t for t in self.biz.staff if sid in t.services and staff_id in (None, t.id)]
    def _mask(self, t, date):
        k = (t.id, date)
        if k not in self.masks:
            a, b = to_units(t.start), to_units(t.end)
            self.masks[k] = slots.mask_busy(slots.mask_busy(slots.FULL, 0, a), b, slots.UNITS - b)
        return self.masks[k]
    def _need(self, s): return ceil5(s.duration_min) + ceil5(self.biz.rules.buffer_min)
    def _log(self, kind, **kw): self.events.append({"t": time.time(), "kind": kind, **kw})
    @staticmethod
    def _pub(b): return {k: v for k, v in b.items() if k != "customer"}
    def _free(self, b):
        t = next(x for x in self.biz.staff if x.id == b["staff_id"]); u = to_units(b["time"])
        k = (t.id, b["date"]); self.masks[k] = self._mask(t, b["date"]) | (((1 << self._need(self.svc(b["service_id"]))) - 1) << u)

    def availability(self, sid, date, at="10:00", staff_id=None):
        s = self.svc(sid); L, B = ceil5(s.duration_min), ceil5(self.biz.rules.buffer_min)
        req, out = to_units(at), []
        with self.lock:
            self._expire()
            for t in self.staff_for(sid, staff_id):
                m = self._mask(t, date)
                for u in slots.rank(slots.free_starts([m], L, B), req, L, m):
                    out.append((abs(u - req), {"time": slots.unit_to_hhmm(u), "staff_id": t.id, "staff": t.name}))
        return [o for _, o in sorted(out, key=lambda x: x[0])[:3]]

    def book(self, cust, sid, date, at, staff_id=None):
        key = IdempotencyStore.key(cust, "book", [sid, date, at, staff_id])
        r = self.idem.run(key, lambda: self._book(cust, sid, date, at, staff_id))
        if not r["ok"]: self.idem._d.pop(key, None)   # never cache failures
        return r
    def _book(self, cust, sid, date, at, staff_id):
        s = self.svc(sid)
        if not s: return {"ok": False, "error": "unknown_service"}
        u, need = to_units(at), self._need(s)
        with self.lock:
            self._expire()
            for t in self.staff_for(sid, staff_id):
                m = self._mask(t, date)
                if u + need <= slots.UNITS and all(m >> (u + i) & 1 for i in range(need)):
                    self.masks[(t.id, date)] = slots.mask_busy(m, u, need)
                    return self._record(cust, s, t, date, at, "BOOKED")
            return {"ok": False, "error": "slot_taken"}
    def _record(self, cust, s, t, date, at, kind):
        b = {"id": f"B{next(self.ids)}", "customer": cust, "service_id": s.id, "service": s.name,
             "staff_id": t.id, "staff": t.name, "date": date, "time": at, "price": s.price, "status": "BOOKED"}
        self.bookings[b["id"]] = b; self._log(kind, booking=b["id"], customer=cust, price=s.price)
        return {"ok": True, "booking": self._pub(b)}

    def cancel(self, cust, bid):
        return self.idem.run(IdempotencyStore.key(cust, "cancel", bid), lambda: self._cancel(cust, bid))
    def _cancel(self, cust, bid):
        with self.lock:
            b = self.bookings.get(bid)
            if not b or b["customer"] != cust: return {"ok": False, "error": "not_found"}
            b["status"] = "CANCELLED"; self._log("CANCELLED", booking=bid, customer=cust, price=b["price"])
            offered = self._offer(b) is not None
            if not offered: self._free(b)
            return {"ok": True, "booking": self._pub(b), "offered_to_waitlist": offered}

    def join_waitlist(self, cust, sid, reliability=0.8):
        with self.lock:
            if not any(w["customer"] == cust and w["service_id"] == sid for w in self.waitlist):
                self.waitlist.append({"customer": cust, "service_id": sid, "joined": time.time(), "reliability": reliability})
            return {"ok": True, "waitlisted": True}
    def _offer(self, b):
        c = [w for w in self.waitlist if w["service_id"] == b["service_id"] and w["customer"] != b.get("customer")]
        if not c: return None
        now = time.time()
        w = max(c, key=lambda w: 0.5 * min((now - w["joined"]) / 3600, 1) + 0.5 * w["reliability"])
        self.waitlist.remove(w)
        o = {"id": f"O{next(self.ids)}", "customer": w["customer"], "service_id": b["service_id"], "staff_id": b["staff_id"],
             "date": b["date"], "time": b["time"], "price": b["price"], "status": "OFFERED",
             "expires": now + self.biz.rules.hold_offer_minutes * 60}
        self.offers[o["id"]] = o; self._log("OFFERED", offer=o["id"], customer=o["customer"]); return o
    def _expire(self):
        for o in list(self.offers.values()):
            if o["status"] == "OFFERED" and o["expires"] < time.time():
                o["status"] = "EXPIRED"; self._log("EXPIRED", offer=o["id"])
                if not self._offer({**o, "customer": None}): self._free(o)
    def accept_offer(self, cust, oid):
        return self.idem.run(IdempotencyStore.key(cust, "accept", oid), lambda: self._accept(cust, oid))
    def _accept(self, cust, oid):
        with self.lock:
            self._expire(); o = self.offers.get(oid)
            if not o or o["customer"] != cust or o["status"] != "OFFERED": return {"ok": False, "error": "offer_unavailable"}
            o["status"] = "ACCEPTED"; t = next(x for x in self.biz.staff if x.id == o["staff_id"])
            return self._record(cust, self.svc(o["service_id"]), t, o["date"], o["time"], "REFILLED")

    def mine(self, cust):
        with self.lock:
            self._expire(); now = time.time()
            bk = sorted((self._pub(b) for b in self.bookings.values() if b["customer"] == cust and b["status"] == "BOOKED"),
                        key=lambda b: (b["date"], b["time"]))
            of = [{k: v for k, v in o.items() if k not in ("customer", "expires")} | {"seconds_left": int(o["expires"] - now)}
                  for o in self.offers.values() if o["customer"] == cust and o["status"] == "OFFERED"]
            wl = [w["service_id"] for w in self.waitlist if w["customer"] == cust]
            return {"bookings": bk, "offers": of, "waitlist": wl}

    def stats(self):
        n = lambda k: sum(e["kind"] == k for e in self.events)
        ref, can = n("REFILLED"), n("CANCELLED")
        return {"source": "live test events", "bookings": n("BOOKED") + ref, "cancellations": can, "offers": n("OFFERED"),
                "refilled": ref, "refill_rate": round(ref / can, 2) if can else 0,
                "recovered_revenue": sum(e["price"] for e in self.events if e["kind"] == "REFILLED")}
