"""DynamoDB persistence implementation for Store interface."""
import datetime as dt
from decimal import Decimal
import json
import os
import re
import secrets
import time
from uuid import uuid4
import zoneinfo
import boto3
from botocore.exceptions import ClientError

from .store_base import BaseStore
from .engines import slots
from infra.tables import get_table_name, create_tables

def to_units(hhmm):
    h, m = map(int, hhmm.split(":"))
    return h * 12 + m // 5

def ceil5(x):
    return -(-x // 5)

def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40]

def to_dynamo(val):
    if isinstance(val, float):
        return Decimal(str(val))
    if isinstance(val, dict):
        return {k: to_dynamo(v) for k, v in val.items()}
    if isinstance(val, list):
        return [to_dynamo(v) for v in val]
    return val

def from_dynamo(val):
    if isinstance(val, Decimal):
        return int(val) if val % 1 == 0 else float(val)
    if isinstance(val, dict):
        return {k: from_dynamo(v) for k, v in val.items()}
    if isinstance(val, list):
        return [from_dynamo(v) for v in val]
    return val

class DynamoStore(BaseStore):
    """DynamoDB-backed store with ACID transactions and optimistic locking."""

    def __init__(self, biz):
        self.biz = biz
        self.business_id = getattr(biz, "id", None) or slug(biz.name)
        self.prefix = os.environ.get("DYNAMO_TABLE_PREFIX", "")
        self.dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
        self.client = self.dynamodb.meta.client
        # Ensure tables exist
        create_tables(self.dynamodb, self.prefix)
        self.slots_table = self.dynamodb.Table(get_table_name("Slots", self.prefix))
        self.bookings_table = self.dynamodb.Table(get_table_name("Bookings", self.prefix))
        self.waitlist_table = self.dynamodb.Table(get_table_name("Waitlist", self.prefix))
        self.idempotency_table = self.dynamodb.Table(get_table_name("Idempotency", self.prefix))
        self.events_table = self.dynamodb.Table(get_table_name("Events", self.prefix))
        self.business_table = self.dynamodb.Table(get_table_name("Business", self.prefix))

    def svc(self, sid):
        return next((s for s in self.biz.services if s.id == sid), None)

    def staff_for(self, sid, staff_id=None):
        return [t for t in self.biz.staff if sid in t.services and staff_id in (None, t.id)]

    def _need(self, s):
        return ceil5(s.duration_min) + ceil5(self.biz.rules.buffer_min)

    def _get_tz(self):
        try:
            return zoneinfo.ZoneInfo(self.biz.timezone)
        except Exception:
            return dt.timezone.utc

    def _log(self, kind, **kw):
        t = time.time()
        eid = uuid4().hex[:12]
        item = {
            "business_id": self.business_id,
            "timestamp_event_id": f"{t:017.6f}#{eid}",
            "kind": kind,
            "t": Decimal(str(t)),
            **{k: to_dynamo(v) for k, v in kw.items()},
        }
        self.events_table.put_item(Item=item)

    @staticmethod
    def _pub(b):
        return {k: v for k, v in b.items() if k not in ("customer", "customer_id", "start_time_booking_id")}

    def _get_mask_item(self, t, date):
        pk = f"{self.business_id}#{t.id}#{date}"
        try:
            res = self.slots_table.get_item(Key={"slot_pk": pk, "start_unit": -1})
            item = res.get("Item")
            if item:
                return int(item["mask"]), int(item["version"])
        except Exception:
            pass
        a, b = to_units(t.start), to_units(t.end)
        initial_mask = slots.mask_busy(slots.mask_busy(slots.FULL, 0, a), b, slots.UNITS - b)
        return initial_mask, 0

    def _get_idempotency(self, cust, key):
        try:
            r = self.idempotency_table.get_item(Key={"idempotency_key": f"{cust}#{key}"})
            item = r.get("Item")
            if item and item.get("ttl", 0) > time.time():
                return json.loads(item["result"])
        except Exception:
            pass
        return None

    def _save_idempotency(self, cust, key, result):
        if not result.get("ok"):
            return
        try:
            self.idempotency_table.put_item(
                Item={
                    "idempotency_key": f"{cust}#{key}",
                    "result": json.dumps(result),
                    "ttl": int(time.time() + 86400),
                }
            )
        except Exception:
            pass

    def availability(self, sid, date, at="10:00", staff_id=None):
        s = self.svc(sid)
        if not s:
            return []
        L, B = ceil5(s.duration_min), ceil5(self.biz.rules.buffer_min)
        req, out = to_units(at), []
        self._expire()
        for t in self.staff_for(sid, staff_id):
            m, _ = self._get_mask_item(t, date)
            for u in slots.rank(slots.free_starts([m], L, B), req, L, m):
                out.append((abs(u - req), {"time": slots.unit_to_hhmm(u), "staff_id": t.id, "staff": t.name}))
        return [o for _, o in sorted(out, key=lambda x: x[0])[:3]]

    def book(self, cust, sid, date, at, staff_id=None):
        key = f"book#{sid}#{date}#{at}#{staff_id or ''}"
        cached = self._get_idempotency(cust, key)
        if cached:
            return cached
        res = self._book(cust, sid, date, at, staff_id)
        if res.get("ok"):
            self._save_idempotency(cust, key, res)
        return res

    def _book(self, cust, sid, date, at, staff_id):
        s = self.svc(sid)
        if not s:
            return {"ok": False, "error": "unknown_service"}
        tz = self._get_tz()
        now = dt.datetime.now(tz)
        b_dt = dt.datetime.combine(dt.date.fromisoformat(date), dt.time.fromisoformat(at), tzinfo=tz)
        if b_dt < now:
            return {"ok": False, "error": "invalid_date", "message": "Cannot book an appointment in the past."}
        u, need = to_units(at), self._need(s)
        self._expire()

        for t in self.staff_for(sid, staff_id):
            m, version = self._get_mask_item(t, date)
            if u + need <= slots.UNITS and all(m >> (u + i) & 1 for i in range(need)):
                new_mask = slots.mask_busy(m, u, need)
                bid = f"B{int(time.time() * 1000) % 100000000}{secrets.randbelow(1000)}"
                pk = f"{self.business_id}#{t.id}#{date}"

                # Build DynamoDB Transaction items
                # 1. Update/Put mask with optimistic locking on version
                if version == 0:
                    mask_action = {
                        "Put": {
                            "TableName": get_table_name("Slots", self.prefix),
                            "Item": {
                                "slot_pk": {"S": pk},
                                "start_unit": {"N": "-1"},
                                "mask": {"N": str(new_mask)},
                                "version": {"N": "1"},
                            },
                            "ConditionExpression": "attribute_not_exists(slot_pk)",
                        }
                    }
                else:
                    mask_action = {
                        "Update": {
                            "TableName": get_table_name("Slots", self.prefix),
                            "Key": {"slot_pk": {"S": pk}, "start_unit": {"N": "-1"}},
                            "UpdateExpression": "SET #m = :new_m, #v = #v + :inc",
                            "ConditionExpression": "#v = :expected_v",
                            "ExpressionAttributeNames": {"#m": "mask", "#v": "version"},
                            "ExpressionAttributeValues": {
                                ":new_m": {"N": str(new_mask)},
                                ":inc": {"N": "1"},
                                ":expected_v": {"N": str(version)},
                            },
                        }
                    }

                # 2. Put Slot Item
                slot_action = {
                    "Put": {
                        "TableName": get_table_name("Slots", self.prefix),
                        "Item": {
                            "slot_pk": {"S": pk},
                            "start_unit": {"N": str(u)},
                            "status": {"S": "BOOKED"},
                            "booking_id": {"S": bid},
                            "customer": {"S": cust},
                            "service_id": {"S": s.id},
                            "end_unit": {"N": str(u + need)},
                        },
                        "ConditionExpression": "attribute_not_exists(slot_pk) OR #st = :free",
                        "ExpressionAttributeNames": {"#st": "status"},
                        "ExpressionAttributeValues": {":free": {"S": "FREE"}},
                    }
                }

                # 3. Put Booking Item
                booking_item = {
                    "customer_id": cust,
                    "start_time_booking_id": f"{date}T{at}#{bid}",
                    "id": bid,
                    "business_id": self.business_id,
                    "service_id": s.id,
                    "service": s.name,
                    "staff_id": t.id,
                    "staff": t.name,
                    "date": date,
                    "time": at,
                    "price": s.price,
                    "status": "BOOKED",
                }
                booking_action = {
                    "Put": {
                        "TableName": get_table_name("Bookings", self.prefix),
                        "Item": {
                            "customer_id": {"S": cust},
                            "start_time_booking_id": {"S": f"{date}T{at}#{bid}"},
                            "id": {"S": bid},
                            "business_id": {"S": self.business_id},
                            "service_id": {"S": s.id},
                            "service": {"S": s.name},
                            "staff_id": {"S": t.id},
                            "staff": {"S": t.name},
                            "date": {"S": date},
                            "time": {"S": at},
                            "price": {"N": str(s.price)},
                            "status": {"S": "BOOKED"},
                        },
                    }
                }

                # 4. Put Event Item
                event_action = {
                    "Put": {
                        "TableName": get_table_name("Events", self.prefix),
                        "Item": {
                            "business_id": {"S": self.business_id},
                            "timestamp_event_id": {"S": f"{time.time():017.6f}#{bid}"},
                            "kind": {"S": "BOOKED"},
                            "booking": {"S": bid},
                            "customer": {"S": cust},
                            "price": {"N": str(s.price)},
                        },
                    }
                }

                try:
                    self.client.transact_write_items(
                        TransactItems=[mask_action, slot_action, booking_action, event_action]
                    )
                    return {"ok": True, "booking": self._pub(booking_item)}
                except ClientError as e:
                    if e.response["Error"]["Code"] == "TransactionCanceledException":
                        continue
                    raise

        return {"ok": False, "error": "slot_taken"}

    def _find_booking(self, cust, bid):
        try:
            r = self.bookings_table.query(
                KeyConditionExpression="customer_id = :c",
                ExpressionAttributeValues={":c": cust},
            )
            for item in r.get("Items", []):
                if item.get("id") == bid:
                    return from_dynamo(item)
        except Exception:
            pass
        return None

    def cancel(self, cust, bid):
        key = f"cancel#{bid}"
        cached = self._get_idempotency(cust, key)
        if cached:
            return cached
        res = self._cancel(cust, bid)
        if res.get("ok"):
            self._save_idempotency(cust, key, res)
        return res

    def _cancel(self, cust, bid):
        b = self._find_booking(cust, bid)
        if not b or b["status"] != "BOOKED":
            return {"ok": False, "error": "not_found"}
        tz = self._get_tz()
        now = dt.datetime.now(tz)
        b_dt = dt.datetime.combine(dt.date.fromisoformat(b["date"]), dt.time.fromisoformat(b["time"]), tzinfo=tz)
        if b_dt - now < dt.timedelta(hours=self.biz.rules.cancel_window_hours):
            return {"ok": False, "error": "cancel_window_passed", "message": f"Cannot cancel within {self.biz.rules.cancel_window_hours} hours of appointment."}

        # Update booking status in Bookings table
        self.bookings_table.update_item(
            Key={"customer_id": cust, "start_time_booking_id": b["start_time_booking_id"]},
            UpdateExpression="SET #st = :canc",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={":canc": "CANCELLED"},
        )
        b["status"] = "CANCELLED"
        self._log("CANCELLED", booking=bid, customer=cust, price=b["price"])

        # Try to offer slot to waitlist
        offered = self._offer(b) is not None
        if not offered:
            self._free(b)
        return {"ok": True, "booking": self._pub(b), "offered_to_waitlist": offered}

    def _free(self, b):
        t = next((x for x in self.biz.staff if x.id == b["staff_id"]), None)
        if not t:
            return
        u = to_units(b["time"])
        s = self.svc(b["service_id"])
        need = self._need(s) if s else 6
        pk = f"{self.business_id}#{t.id}#{b['date']}"
        m, version = self._get_mask_item(t, b["date"])
        freed_mask = m | (((1 << need) - 1) << u)
        try:
            self.slots_table.update_item(
                Key={"slot_pk": pk, "start_unit": -1},
                UpdateExpression="SET #m = :new_m, #v = #v + :inc",
                ExpressionAttributeNames={"#m": "mask", "#v": "version"},
                ExpressionAttributeValues={":new_m": Decimal(str(freed_mask)), ":inc": 1},
            )
            self.slots_table.update_item(
                Key={"slot_pk": pk, "start_unit": u},
                UpdateExpression="SET #st = :free",
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={":free": "FREE"},
            )
        except Exception:
            pass

    def join_waitlist(self, cust, sid, reliability=0.8):
        pk = f"{self.business_id}#{sid}"
        # Check if already in waitlist
        try:
            res = self.waitlist_table.query(
                KeyConditionExpression="business_service = :bs",
                ExpressionAttributeValues={":bs": pk},
            )
            for item in res.get("Items", []):
                if item.get("customer") == cust:
                    return {"ok": True, "waitlisted": True}
        except Exception:
            pass
        now = time.time()
        sk = f"{now:017.6f}#{cust}"
        self.waitlist_table.put_item(
            Item={
                "business_service": pk,
                "joined_customer": sk,
                "business_id": self.business_id,
                "service_id": sid,
                "customer": cust,
                "joined": Decimal(str(now)),
                "reliability": Decimal(str(reliability)),
            }
        )
        return {"ok": True, "waitlisted": True}

    def _offer(self, b):
        pk = f"{self.business_id}#{b['service_id']}"
        try:
            res = self.waitlist_table.query(
                KeyConditionExpression="business_service = :bs",
                ExpressionAttributeValues={":bs": pk},
            )
            candidates = [from_dynamo(it) for it in res.get("Items", []) if it.get("customer") != b.get("customer")]
        except Exception:
            candidates = []
        if not candidates:
            return None
        now = time.time()
        w = max(candidates, key=lambda w: 0.5 * min((now - w["joined"]) / 3600, 1) + 0.5 * w["reliability"])
        # Remove from waitlist
        try:
            self.waitlist_table.delete_item(
                Key={"business_service": pk, "joined_customer": f"{w['joined']:017.6f}#{w['customer']}"}
            )
        except Exception:
            pass
        oid = f"O{int(now * 1000) % 100000000}{secrets.randbelow(1000)}"
        expires_at = now + self.biz.rules.hold_offer_minutes * 60
        offer_item = {
            "customer_id": w["customer"],
            "start_time_booking_id": f"OFFER#{oid}",
            "id": oid,
            "business_id": self.business_id,
            "service_id": b["service_id"],
            "staff_id": b["staff_id"],
            "date": b["date"],
            "time": b["time"],
            "price": b["price"],
            "status": "OFFERED",
            "expires_at": Decimal(str(expires_at)),
            "ttl": int(expires_at + 86400),
        }
        self.bookings_table.put_item(Item=to_dynamo(offer_item))
        self._log("OFFERED", offer=oid, customer=w["customer"])
        return offer_item

    def _expire(self):
        # Safety net lazy expiry of offers
        now = time.time()
        try:
            res = self.bookings_table.scan(
                FilterExpression="#st = :offered AND expires_at < :now",
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={":offered": "OFFERED", ":now": Decimal(str(now))},
            )
            for item in res.get("Items", []):
                oid = item["id"]
                cust = item["customer_id"]
                self.bookings_table.update_item(
                    Key={"customer_id": cust, "start_time_booking_id": item["start_time_booking_id"]},
                    UpdateExpression="SET #st = :exp",
                    ExpressionAttributeNames={"#st": "status"},
                    ExpressionAttributeValues={":exp": "EXPIRED"},
                )
                self._log("EXPIRED", offer=oid)
                # Next candidate or free
                raw = from_dynamo(item)
                if not self._offer({**raw, "customer": None}):
                    self._free(raw)
        except Exception:
            pass

    def accept_offer(self, cust, oid):
        key = f"accept#{oid}"
        cached = self._get_idempotency(cust, key)
        if cached:
            return cached
        res = self._accept(cust, oid)
        if res.get("ok"):
            self._save_idempotency(cust, key, res)
        return res

    def _accept(self, cust, oid):
        self._expire()
        try:
            r = self.bookings_table.get_item(Key={"customer_id": cust, "start_time_booking_id": f"OFFER#{oid}"})
            item = r.get("Item")
        except Exception:
            item = None
        if not item:
            return {"ok": False, "error": "offer_unavailable"}
        o = from_dynamo(item)
        if o.get("status") != "OFFERED" or o.get("expires_at", 0) < time.time():
            return {"ok": False, "error": "offer_unavailable"}

        # Mark offer as ACCEPTED
        self.bookings_table.update_item(
            Key={"customer_id": cust, "start_time_booking_id": f"OFFER#{oid}"},
            UpdateExpression="SET #st = :acc",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={":acc": "ACCEPTED"},
        )
        s = self.svc(o["service_id"])
        t = next((x for x in self.biz.staff if x.id == o["staff_id"]), self.biz.staff[0])
        bid = f"B{int(time.time() * 1000) % 100000000}{secrets.randbelow(1000)}"
        new_booking = {
            "customer_id": cust,
            "start_time_booking_id": f"{o['date']}T{o['time']}#{bid}",
            "id": bid,
            "business_id": self.business_id,
            "service_id": s.id,
            "service": s.name,
            "staff_id": t.id,
            "staff": t.name,
            "date": o["date"],
            "time": o["time"],
            "price": o["price"],
            "status": "BOOKED",
        }
        self.bookings_table.put_item(Item=to_dynamo(new_booking))
        self._log("REFILLED", booking=bid, customer=cust, price=o["price"])
        return {"ok": True, "booking": self._pub(new_booking)}

    def reschedule(self, cust, bid, date, at, staff_id=None):
        key = f"reschedule#{bid}#{date}#{at}#{staff_id or ''}"
        cached = self._get_idempotency(cust, key)
        if cached:
            return cached
        res = self._reschedule(cust, bid, date, at, staff_id)
        if res.get("ok"):
            self._save_idempotency(cust, key, res)
        return res

    def _reschedule(self, cust, bid, date, at, staff_id):
        self._expire()
        b = self._find_booking(cust, bid)
        if not b or b["status"] != "BOOKED":
            return {"ok": False, "error": "not_found", "message": "Booking not found or not active."}
        tz = self._get_tz()
        now = dt.datetime.now(tz)
        b_dt = dt.datetime.combine(dt.date.fromisoformat(b["date"]), dt.time.fromisoformat(b["time"]), tzinfo=tz)
        if b_dt - now < dt.timedelta(hours=self.biz.rules.cancel_window_hours):
            return {"ok": False, "error": "cancel_window_passed", "message": f"Cannot reschedule within {self.biz.rules.cancel_window_hours} hours of appointment."}

        s = self.svc(b["service_id"])
        if not s:
            return {"ok": False, "error": "unknown_service"}
        u, need = to_units(at), self._need(s)

        # Try to reserve new slot atomically
        new_booking_res = None
        for t in self.staff_for(s.id, staff_id):
            m, version = self._get_mask_item(t, date)
            if u + need <= slots.UNITS and all(m >> (u + i) & 1 for i in range(need)):
                new_mask = slots.mask_busy(m, u, need)
                new_bid = f"B{int(time.time() * 1000) % 100000000}{secrets.randbelow(1000)}"
                pk = f"{self.business_id}#{t.id}#{date}"

                if version == 0:
                    mask_action = {
                        "Put": {
                            "TableName": get_table_name("Slots", self.prefix),
                            "Item": {
                                "slot_pk": {"S": pk},
                                "start_unit": {"N": "-1"},
                                "mask": {"N": str(new_mask)},
                                "version": {"N": "1"},
                            },
                            "ConditionExpression": "attribute_not_exists(slot_pk)",
                        }
                    }
                else:
                    mask_action = {
                        "Update": {
                            "TableName": get_table_name("Slots", self.prefix),
                            "Key": {"slot_pk": {"S": pk}, "start_unit": {"N": "-1"}},
                            "UpdateExpression": "SET #m = :new_m, #v = #v + :inc",
                            "ConditionExpression": "#v = :expected_v",
                            "ExpressionAttributeNames": {"#m": "mask", "#v": "version"},
                            "ExpressionAttributeValues": {
                                ":new_m": {"N": str(new_mask)},
                                ":inc": {"N": "1"},
                                ":expected_v": {"N": str(version)},
                            },
                        }
                    }

                slot_action = {
                    "Put": {
                        "TableName": get_table_name("Slots", self.prefix),
                        "Item": {
                            "slot_pk": {"S": pk},
                            "start_unit": {"N": str(u)},
                            "status": {"S": "BOOKED"},
                            "booking_id": {"S": new_bid},
                            "customer": {"S": cust},
                            "service_id": {"S": s.id},
                            "end_unit": {"N": str(u + need)},
                        },
                        "ConditionExpression": "attribute_not_exists(slot_pk) OR #st = :free",
                        "ExpressionAttributeNames": {"#st": "status"},
                        "ExpressionAttributeValues": {":free": {"S": "FREE"}},
                    }
                }

                new_booking_item = {
                    "customer_id": cust,
                    "start_time_booking_id": f"{date}T{at}#{new_bid}",
                    "id": new_bid,
                    "business_id": self.business_id,
                    "service_id": s.id,
                    "service": s.name,
                    "staff_id": t.id,
                    "staff": t.name,
                    "date": date,
                    "time": at,
                    "price": s.price,
                    "status": "BOOKED",
                }
                booking_action = {
                    "Put": {
                        "TableName": get_table_name("Bookings", self.prefix),
                        "Item": {
                            "customer_id": {"S": cust},
                            "start_time_booking_id": {"S": f"{date}T{at}#{new_bid}"},
                            "id": {"S": new_bid},
                            "business_id": {"S": self.business_id},
                            "service_id": {"S": s.id},
                            "service": {"S": s.name},
                            "staff_id": {"S": t.id},
                            "staff": {"S": t.name},
                            "date": {"S": date},
                            "time": {"S": at},
                            "price": {"N": str(s.price)},
                            "status": {"S": "BOOKED"},
                        },
                    }
                }

                # Also mark old booking as RESCHEDULED in the same transaction
                old_booking_action = {
                    "Update": {
                        "TableName": get_table_name("Bookings", self.prefix),
                        "Key": {
                            "customer_id": {"S": cust},
                            "start_time_booking_id": {"S": b["start_time_booking_id"]},
                        },
                        "UpdateExpression": "SET #st = :resched",
                        "ConditionExpression": "#st = :booked",
                        "ExpressionAttributeNames": {"#st": "status"},
                        "ExpressionAttributeValues": {
                            ":resched": {"S": "RESCHEDULED"},
                            ":booked": {"S": "BOOKED"},
                        },
                    }
                }

                try:
                    self.client.transact_write_items(
                        TransactItems=[mask_action, slot_action, booking_action, old_booking_action]
                    )
                    new_booking_res = new_booking_item
                    break
                except ClientError as e:
                    if e.response["Error"]["Code"] == "TransactionCanceledException":
                        continue
                    raise

        if not new_booking_res:
            return {"ok": False, "error": "slot_taken", "message": "Requested slot is already taken."}

        # Release old slot
        self._log("RESCHEDULED", booking=bid, customer=cust, price=b["price"])
        offered = self._offer(b) is not None
        if not offered:
            self._free(b)

        return {
            "ok": True,
            "rescheduled_from": bid,
            "booking": self._pub(new_booking_res),
            "offered_old_to_waitlist": offered,
        }

    def mine(self, cust):
        self._expire()
        now = time.time()
        bk, of = [], []
        try:
            r = self.bookings_table.query(
                KeyConditionExpression="customer_id = :c",
                ExpressionAttributeValues={":c": cust},
            )
            for raw in r.get("Items", []):
                item = from_dynamo(raw)
                sk = item.get("start_time_booking_id", "")
                if sk.startswith("OFFER#") and item.get("status") == "OFFERED":
                    exp = float(item.get("expires_at", 0))
                    if exp > now:
                        of_dict = {k: v for k, v in item.items() if k not in ("customer_id", "expires_at", "ttl", "start_time_booking_id")}
                        of_dict["seconds_left"] = int(exp - now)
                        of.append(of_dict)
                elif not sk.startswith("OFFER#") and item.get("status") == "BOOKED":
                    bk.append(self._pub(item))
        except Exception:
            pass

        # Sort bookings by date and time
        bk.sort(key=lambda b: (b.get("date", ""), b.get("time", "")))

        # Waitlist for customer
        wl = []
        try:
            for s in self.biz.services:
                pk = f"{self.business_id}#{s.id}"
                w_res = self.waitlist_table.query(
                    KeyConditionExpression="business_service = :bs",
                    ExpressionAttributeValues={":bs": pk},
                )
                for item in w_res.get("Items", []):
                    if item.get("customer") == cust:
                        wl.append(s.id)
        except Exception:
            pass

        return {"bookings": bk, "offers": of, "waitlist": wl}

    def stats(self):
        events = []
        try:
            res = self.events_table.query(
                KeyConditionExpression="business_id = :b",
                ExpressionAttributeValues={":b": self.business_id},
            )
            events = [from_dynamo(it) for it in res.get("Items", [])]
        except Exception:
            pass

        n = lambda k: sum(e.get("kind") == k for e in events)
        ref, can = n("REFILLED"), n("CANCELLED")
        return {
            "source": "live test events",
            "bookings": n("BOOKED") + ref,
            "cancellations": can,
            "offers": n("OFFERED"),
            "refilled": ref,
            "refill_rate": round(ref / can, 2) if can else 0,
            "recovered_revenue": sum(e.get("price", 0) for e in events if e.get("kind") == "REFILLED"),
        }
