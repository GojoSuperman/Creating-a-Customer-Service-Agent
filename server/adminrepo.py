# -*- coding: utf-8 -*-
"""어드민 조회 전용 저장소. 읽기 쿼리만 둔다 (쓰기 SQL 금지)."""
import threading

from server.repo import _row, normalize_phone

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
            # 날짜만 받으므로 그날 끝까지 포함시킨다
            cond.append("o.ordered_at <= ?"); params.append(f"{date_to}T23:59:59")
        select = ("select o.order_id, o.ordered_at, o.status, o.status_detail, o.order_amount, "
                  "o.customer_id, o.return_id, c.name as customer_name "
                  "from orders o left join customers c on c.customer_id = o.customer_id")
        return self._page(select, "select count(*) from orders o", self._where(cond), tuple(params),
                           "order by o.ordered_at desc", page, size)

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
                           "order by r.requested_at desc", page, size)

    def calls(self, *, page=1, size=PAGE_SIZE):
        select = ("select l.call_id, l.customer_id, l.started_at, l.ended_at, l.turns, "
                  "c.name as customer_name from call_logs l "
                  "left join customers c on c.customer_id = l.customer_id")
        rows, total = self._page(select, "select count(*) from call_logs l", "", (),
                                  "order by l.started_at desc", page, size)
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
            cond.append("(name like ? escape '\\' or phone = ? or replace(phone,'-','') like ? escape '\\')")
            params += [f"%{escaped}%", normalize_phone(q), f"%{escaped_digits}%"]
        return self._page("select customer_id, name, phone, address_region, joined_at from customers",
                           "select count(*) from customers", self._where(cond), tuple(params),
                           "order by customer_id", page, size)
