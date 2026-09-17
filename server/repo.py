# -*- coding: utf-8 -*-
"""SQLite 읽기 저장소. 도구가 쓰던 dict 모양을 그대로 돌려준다 (JSON 열 파싱, 0/1 → bool)."""
import json
import re
import sqlite3
import threading
from pathlib import Path
from typing import Optional

JSON_COLS = {"components", "options", "size_chart", "individual_prices", "turns", "size_matching", "exchange_target"}
BOOL_COLS = {"is_set", "has_quality_cert", "made_to_order", "individual_purchase_allowed", "return_allowed", "soldout",
             "is_external_channel", "free_shipping_applied", "invoice_printed", "exchange_available", "convert_to_refund",
             "is_soldout", "is_confirmed", "notify_available", "available", "requires_unopened"}


def normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("82"):
        digits = "0" + digits[2:]
    if len(digits) == 11:
        return f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"
    return raw.strip()


def _row(r: Optional[sqlite3.Row]) -> Optional[dict]:
    if r is None:
        return None
    d = dict(r)
    for k, v in d.items():
        if k in JSON_COLS and isinstance(v, str):
            d[k] = json.loads(v)
        elif k == "made_to_order_days" and isinstance(v, str) and (v.startswith("[") or v.startswith("{")):
            d[k] = json.loads(v)
        elif k in BOOL_COLS and v is not None:
            d[k] = bool(v)
    return d


class Repo:
    def __init__(self, db_path: Path):
        self.con = sqlite3.connect(str(db_path), check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self._lock = threading.RLock()

    def _one(self, sql, *args):
        with self._lock:
            return _row(self.con.execute(sql, args).fetchone())

    def _all(self, sql, *args):
        with self._lock:
            return [_row(r) for r in self.con.execute(sql, args).fetchall()]

    def product(self, pid): return self._one("select * from products where product_id=?", pid)
    def products(self): return self._all("select * from products order by product_id")

    def order(self, oid):
        o = self._one("select * from orders where order_id=?", oid)
        if o:
            o["items"] = self._all("select product_id,name,option,qty,price from order_items where order_id=? order by id", oid)
        return o

    def _return(self, r):
        if r:
            r["stage_history"] = self._all("select stage,date from return_stage_history where return_id=? order by id", r["return_id"])
        return r

    def return_by_id(self, rid): return self._return(self._one("select * from returns where return_id=?", rid))
    def return_by_order(self, oid): return self._return(self._one("select * from returns where order_id=? order by requested_at desc limit 1", oid))
    def restock(self, pid): return self._one("select * from restock where product_id=?", pid)
    def categories(self): return {r["key"]: r for r in self._all("select * from categories")}
    def same_day(self): return {r["region"]: r for r in self._all("select * from same_day_delivery")}
    def shipment_events(self, oid): return self._all("select at,status,location from shipment_events where order_id=? order by id", oid)

    def customer(self, cid): return self._one("select * from customers where customer_id=?", cid)
    def customer_by_phone(self, phone): return self._one("select * from customers where phone=?", normalize_phone(phone))
    def all_customer_ids(self): return [r["customer_id"] for r in self._all("select customer_id from customers order by customer_id")]

    def recent_orders(self, cid, limit=3):
        out = []
        for o in self._all("select order_id,ordered_at,status,status_detail,order_amount from orders where customer_id=? order by ordered_at desc limit ?", cid, limit):
            items = self._all("select name,qty from order_items where order_id=? order by id", o["order_id"])
            o["items_summary"] = ", ".join(f"{i['name']}×{i['qty']}" if i["qty"] > 1 else i["name"] for i in items)
            out.append(o)
        return out

    IN_PROGRESS_EXCLUDED = ("배송완료", "반품완료", "교환완료", "취소")

    def customer_profile(self, cid, limit=5):
        """상담원 화면용 고객 프로필. 주소·전화 등 개인정보를 포함하므로 프롬프트에는 넣지 않는다.
        orders 는 최신순이며, 진행 중(배송완료가 아닌) 주문에는 배송 이력(events)과 반품 단계(return_)를 붙인다."""
        c = self.customer(cid)
        if not c:
            return None
        orders = []
        for o in self._all("""select order_id,ordered_at,status,status_detail,order_amount,shipping_fee,courier,tracking_no,
                                    invoice_printed,expected_ship_date,shipped_at,expected_delivery,delivered_at,delay_days,delay_reason,
                                    return_id,is_external_channel,external_channel_name,note
                               from orders where customer_id=? order by ordered_at desc limit ?""", cid, limit):
            items = self._all("select name,option,qty,price from order_items where order_id=? order by id", o["order_id"])
            o["items"] = items
            o["items_summary"] = ", ".join(f"{i['name']}×{i['qty']}" if i["qty"] > 1 else i["name"] for i in items)
            o["in_progress"] = o["status"] not in self.IN_PROGRESS_EXCLUDED
            if o["in_progress"]:
                o["events"] = self.shipment_events(o["order_id"])
                r = self.return_by_id(o["return_id"]) if o.get("return_id") else None
                o["return_"] = ({"return_id": r["return_id"], "type": r["type"], "stage": r["stage"],
                                 "expected_completion": r.get("expected_completion"), "refund_amount": r.get("refund_amount"),
                                 "stage_history": r.get("stage_history", [])} if r else None)
            orders.append(o)
        return {"customer_id": c["customer_id"], "name": c["name"], "phone": c["phone"],
                "address_region": c.get("address_region"), "address": c.get("address"), "joined_at": c.get("joined_at"),
                "orders": orders, "in_progress_count": sum(1 for o in orders if o["in_progress"])}

    def latest_customer_by_status(self, status: str, today_iso: str, cutoff_iso: str):
        """cutoff 이후 해당 상태의 주문을 가진 고객 중 가장 최근 주문의 고객 1명."""
        return self._one("""select customer_id from orders where status=? and ordered_at >= ? and ordered_at <= ?
                            order by ordered_at desc limit 1""", status, cutoff_iso, today_iso)

    def recently_active_customers(self, today_iso: str, cutoff_iso: str, limit: int = 5):
        """최근 14일 내 배송완료가 아닌 주문이 있는 고객을 최신 주문 순으로 distinct 하게 돌려준다."""
        rows = self._all("""
            select customer_id, max(ordered_at) as last_ordered_at
            from orders
            where ordered_at >= ? and ordered_at <= ? and status not in ('배송완료','반품완료','교환완료')
            group by customer_id
            order by last_ordered_at desc
            limit ?""", cutoff_iso, today_iso, limit)
        return rows

    def log_call(self, call_id, customer_id, started_at):
        with self._lock:
            self.con.execute("insert or replace into call_logs (call_id,customer_id,started_at) values (?,?,?)", (call_id, customer_id, started_at))
            self.con.commit()

    def finish_call(self, call_id, ended_at, turns):
        # If call_id doesn't exist, this is a no-op (update affects 0 rows).
        with self._lock:
            self.con.execute("update call_logs set ended_at=?, turns=? where call_id=?", (ended_at, json.dumps(turns, ensure_ascii=False), call_id))
            self.con.commit()

    def call(self, call_id): return self._one("select * from call_logs where call_id=?", call_id)
