# -*- coding: utf-8 -*-
"""어드민 조회 전용 저장소. 읽기 쿼리만 둔다 (쓰기 SQL 금지)."""
import threading

from server.repo import _row

PAGE_SIZE = 20
# 종결되지 않은 주문. 홈의 "진행중 주문" 집계에 쓴다.
IN_PROGRESS_STATUSES = ("결제완료", "제작중", "배송중", "반품진행", "교환진행")


class AdminRepo:
    def __init__(self, con):
        self.con = con
        self._lock = threading.RLock()

    # ── 내부 헬퍼 ────────────────────────────────────────
    def _all(self, sql, params=()):
        with self._lock:
            return [_row(r) for r in self.con.execute(sql, params).fetchall()]

    def _one(self, sql, params=()):
        with self._lock:
            return _row(self.con.execute(sql, params).fetchone())

    def _count(self, sql, params=()):
        with self._lock:
            return self.con.execute(sql, params).fetchone()[0]

    def _page(self, select_sql, count_sql, where, params, order, page, size):
        page = max(1, int(page or 1))
        size = max(1, int(size or PAGE_SIZE))
        total = self._count(f"{count_sql} {where}", params)
        rows = self._all(f"{select_sql} {where} {order} limit ? offset ?",
                          (*params, size, (page - 1) * size))
        return rows, total

    @staticmethod
    def _where(cond):
        return ("where " + " and ".join(cond)) if cond else ""

    @staticmethod
    def _end_of_day(date_to):
        # 이미 시각(T)을 포함한 값이면 그대로 쓰고, 날짜만이면 그날 끝을 붙인다
        return date_to if "T" in date_to else f"{date_to}T23:59:59"

    # ── 목록 ────────────────────────────────────────────
    def orders(self, *, status=None, customer_id=None, date_from=None, date_to=None, page=1, size=PAGE_SIZE):
        cond, params = [], []
        if status:
            cond.append("o.status = ?"); params.append(status)
        if customer_id:
            cond.append("o.customer_id = ?"); params.append(customer_id)
        if date_from:
            cond.append("o.ordered_at >= ?"); params.append(date_from)
        if date_to:
            # 날짜만 받으면 그날 끝까지 포함시키고, 이미 시각이 있으면 그대로 쓴다
            cond.append("o.ordered_at <= ?"); params.append(self._end_of_day(date_to))
        select = ("select o.order_id, o.ordered_at, o.status, o.status_detail, o.order_amount, "
                  "o.customer_id, o.return_id, c.name as customer_name "
                  "from orders o left join customers c on c.customer_id = o.customer_id")
        return self._page(select, "select count(*) from orders o", self._where(cond), tuple(params),
                           "order by o.ordered_at desc, o.order_id desc", page, size)

    def returns(self, *, stage=None, type=None, page=1, size=PAGE_SIZE):
        cond, params = [], []
        if stage:
            cond.append("r.stage = ?"); params.append(stage)
        if type:
            cond.append("r.type = ?"); params.append(type)
        select = ("select r.return_id, r.order_id, r.type, r.stage, r.requested_at, r.refund_amount, "
                  "r.expected_completion, o.customer_id, c.name as customer_name from returns r "
                  "left join orders o on o.order_id = r.order_id "
                  "left join customers c on c.customer_id = o.customer_id")
        return self._page(select, "select count(*) from returns r", self._where(cond), tuple(params),
                           "order by r.requested_at desc, r.return_id desc", page, size)

    def calls(self, *, page=1, size=PAGE_SIZE):
        select = ("select l.call_id, l.customer_id, l.started_at, l.ended_at, l.turns, "
                  "c.name as customer_name from call_logs l "
                  "left join customers c on c.customer_id = l.customer_id")
        rows, total = self._page(select, "select count(*) from call_logs l", "", (),
                                  "order by l.started_at desc, l.call_id desc", page, size)
        for r in rows:
            turns = r.get("turns") or []
            r["turn_count"] = len(turns)
            r["routes"] = list(dict.fromkeys([t.get("route") for t in turns if t.get("route")]))
        return rows, total

    @staticmethod
    def _like_escape(s):
        # LIKE 패턴에서 %, _ 가 와일드카드로 해석되지 않도록 이스케이프한다.
        return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def customers(self, *, q=None, page=1, size=PAGE_SIZE):
        cond, params = [], []
        if q:
            escaped = self._like_escape(q)
            escaped_digits = self._like_escape(q.replace("-", ""))
            # phone = ? (정규화 정확 일치) 은 아래 replace(...) like ? 에 완전히 포함되므로 제거했다
            cond.append("(name like ? escape '\\' or replace(phone,'-','') like ? escape '\\')")
            params += [f"%{escaped}%", f"%{escaped_digits}%"]
        return self._page("select customer_id, name, phone, address_region, joined_at from customers",
                           "select count(*) from customers", self._where(cond), tuple(params),
                           "order by customer_id", page, size)

    # ── 상세 ────────────────────────────────────────────
    def _stage_history(self, r):
        if r:
            r["stage_history"] = self._all(
                "select stage,date from return_stage_history where return_id=? order by id", (r["return_id"],))
        return r

    def order_detail(self, order_id):
        o = self._one("select * from orders where order_id=?", (order_id,))
        if not o:
            return None
        o["items"] = self._all(
            "select product_id,name,option,qty,price from order_items where order_id=? order by id", (order_id,))
        o["events"] = self._all(
            "select at,status,location from shipment_events where order_id=? order by id", (order_id,))
        o["return_"] = self._stage_history(self._one(
            "select * from returns where order_id=? order by requested_at desc limit 1", (order_id,)))
        o["customer"] = self._one("select * from customers where customer_id=?", (o["customer_id"],)) \
            if o.get("customer_id") else None
        return o

    def return_detail(self, return_id):
        r = self._stage_history(self._one("select * from returns where return_id=?", (return_id,)))
        if not r:
            return None
        r["order"] = self._one("select * from orders where order_id=?", (r["order_id"],))
        r["customer"] = self._one(
            "select c.* from customers c join orders o on o.customer_id = c.customer_id where o.order_id=?",
            (r["order_id"],))
        return r

    def call_detail(self, call_id):
        c = self._one("select * from call_logs where call_id=?", (call_id,))
        if not c:
            return None
        c["turns"] = c.get("turns") or []
        c["customer"] = self._one("select * from customers where customer_id=?", (c["customer_id"],)) \
            if c.get("customer_id") else None
        return c

    def customer_detail(self, customer_id):
        c = self._one("select * from customers where customer_id=?", (customer_id,))
        if not c:
            return None
        c["orders"] = self._all(
            "select order_id, ordered_at, status, order_amount, customer_id, return_id from orders "
            "where customer_id=? order by ordered_at desc", (customer_id,))
        calls = self._all(
            "select call_id, started_at, ended_at, turns from call_logs where customer_id=? "
            "order by started_at desc", (customer_id,))
        for r in calls:
            r["turn_count"] = len(r.get("turns") or [])
        c["calls"] = calls
        return c

    # ── 요약 ────────────────────────────────────────────
    def summary(self, today_iso):
        day = today_iso[:10]
        qs = ",".join("?" * len(IN_PROGRESS_STATUSES))
        recent_calls, _ = self.calls(page=1, size=5)
        return {
            "calls_today": self._count(
                "select count(*) from call_logs where substr(started_at,1,10) = ?", (day,)),
            "orders_in_progress": self._count(
                f"select count(*) from orders where status in ({qs})", IN_PROGRESS_STATUSES),
            "returns_by_stage": self._all(
                "select stage, count(*) as n from returns group by stage order by n desc"),
            "recent_calls": recent_calls,
        }
