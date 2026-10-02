import hashlib, json, threading, time
class IdempotencyStore:
    """In-memory stand-in; DynamoDB conditional put + TTL replaces it later."""
    def __init__(self, ttl=86400): self._d, self._l, self.ttl = {}, threading.Lock(), ttl
    @staticmethod
    def key(customer, tool, args):
        return hashlib.sha256(f"{customer}|{tool}|{json.dumps(args, sort_keys=True)}".encode()).hexdigest()
    def run(self, key, fn):
        with self._l:
            hit = self._d.get(key)
            if hit and hit[0] > time.time(): return hit[1]
            res = fn(); self._d[key] = (time.time() + self.ttl, res); return res

class SlotStore:
    """Conditional write: slot must be FREE (mirrors a DynamoDB ConditionExpression)."""
    def __init__(self): self._s, self._l = {}, threading.Lock()
    def reserve(self, slot, customer):
        with self._l:
            if self._s.get(slot) is not None: return False
            self._s[slot] = customer; return True
