# -*- coding: utf-8 -*-
"""쇼핑몰 고객 화면 저장소. 상품 조회·금액 계산(배송비 포함)·주문 생성을 담당한다.
라우트·화면은 Task 4~5 에서 다룬다."""
import datetime

from server.repo import _row

PAGE_SIZE = 24


class OrderError(Exception):
    """주문을 만들 수 없는 이유. message 와 blocked 는 화면에 그대로 보여준다."""

    def __init__(self, message, blocked=()):
        super().__init__(message)
        self.message = message
        self.blocked = list(blocked)


class ShopRepo:
    def __init__(self, repo, domain):
        self.repo = repo
        self.con = repo.con
        self._lock = repo._lock          # Repo·AdminRepo 와 같은 락 아래에서 읽는다
        self.base_fee = int(domain.fixed_values["base_shipping_fee"])

    # ── 내부 헬퍼 ────────────────────────────────────────
    def _all(self, sql, params=()):
        with self._lock:
            return [_row(r) for r in self.con.execute(sql, params).fetchall()]

    def _count(self, sql, params=()):
        with self._lock:
            return self.con.execute(sql, params).fetchone()[0]

    @staticmethod
    def _like_escape(text):
        # LIKE 패턴에서 %, _ 가 와일드카드로 해석되지 않도록 이스케이프한다.
        return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    # ── 조회 ────────────────────────────────────────────
    def products(self, *, category=None, q=None, page=1, size=PAGE_SIZE):
        cond, params = [], []
        if category:
            cond.append("category = ?")
            params.append(category)
        if q:
            cond.append("name like ? escape '\\'")
            params.append(f"%{self._like_escape(q)}%")
        where = ("where " + " and ".join(cond)) if cond else ""
        page = max(1, int(page or 1))
        size = max(1, int(size or PAGE_SIZE))
        total = self._count(f"select count(*) from products {where}", tuple(params))
        rows = self._all(
            f"select product_id, name, category, price, stock, soldout, soldout_note, options, material "
            f"from products {where} order by product_id limit ? offset ?",
            (*params, size, (page - 1) * size))
        return rows, total

    def product(self, product_id):
        return self.repo.product(product_id)

    def category_list(self):
        cats = self.repo.categories()
        return [{"key": k, "label": v["label"], "free_shipping_threshold": v["free_shipping_threshold"]}
                for k, v in sorted(cats.items())]

    # ── 금액 ────────────────────────────────────────────
    def _shipping(self, subtotal, categories):
        """무료배송 대상이 아닌 카테고리(임계값 NULL, 예: 화장품)가 섞이면 무조건 유료.
        그 외에는 포함된 모든 카테고리의 임계값을 합계가 전부 넘어야 무료(스펙 2.4)."""
        rows = self.repo.categories()
        thresholds = []
        for key in categories:
            row = rows.get(key)
            threshold = row["free_shipping_threshold"] if row else None
            if threshold is None:
                return self.base_fee, False
            thresholds.append(threshold)
        if thresholds and subtotal >= max(thresholds):
            return 0, True
        return self.base_fee, False

    def quote(self, cart):
        """장바구니·주문확인·주문생성이 공통으로 쓰는 금액 계산. 존재하지 않는 상품은 조용히 건너뛴다."""
        lines, subtotal, categories = [], 0, []
        for item in cart:
            p = self.product(item["product_id"])
            if p is None:
                continue
            qty = int(item["qty"])
            amount = p["price"] * qty
            subtotal += amount
            categories.append(p["category"])
            lines.append({
                "product_id": p["product_id"], "name": p["name"], "option": item.get("option"),
                "qty": qty, "price": p["price"], "amount": amount,
                "soldout": bool(p["soldout"]), "stock": p["stock"], "category": p["category"],
            })
        if not lines:
            return {"lines": [], "subtotal": 0, "shipping_fee": 0, "free_shipping_applied": False, "total": 0}
        fee, free = self._shipping(subtotal, dict.fromkeys(categories))
        return {"lines": lines, "subtotal": subtotal, "shipping_fee": fee,
                "free_shipping_applied": free, "total": subtotal + fee}

    # ── 주문 ────────────────────────────────────────────
    def _next_order_id(self):
        row = self.con.execute(
            "select max(cast(substr(order_id, 3) as integer)) from orders where order_id like 'O-%'").fetchone()
        return f"O-{(row[0] or 1000) + 1}"

    @staticmethod
    def _valid_qty(raw):
        """정수로 변환 가능하고 1개 이상인지 확인한다. Task 2 리뷰에서 발견된
        quote() 의 음수 수량(합계가 음수가 되는) 취약점을 주문 경로에서 막는다."""
        if isinstance(raw, bool):
            return None
        try:
            qty = int(raw)
        except (TypeError, ValueError):
            return None
        if qty < 1:
            return None
        return qty

    def create_order(self, customer_id, cart, now=None):
        """한 트랜잭션으로 주문·품목을 쓰고 재고를 줄인다. 하나라도 살 수 없으면 전부 취소한다."""
        if not cart:
            raise OrderError("장바구니가 비어 있습니다.")
        for item in cart:
            if self._valid_qty(item.get("qty")) is None:
                raise OrderError("주문 수량이 올바르지 않습니다.",
                                  [f"{item.get('product_id')} 수량이 올바르지 않습니다."])
        now = now or datetime.datetime.now().isoformat()
        with self._lock:
            try:
                if self.con.in_transaction:
                    # sqlite3 는 앞선 쓰기로 이미 암묵적 트랜잭션을 열어뒀을 수 있다.
                    # 그 쓰기는 이미 완료된 별개의 작업이므로 커밋해 닫고 우리 트랜잭션을 새로 연다.
                    self.con.commit()
                self.con.execute("begin immediate")
                blocked, buyable = [], []
                for item in cart:
                    p = self.repo.product(item["product_id"])
                    qty = self._valid_qty(item["qty"])
                    if p is None:
                        blocked.append(f"{item['product_id']} 상품을 찾을 수 없습니다.")
                    elif p["soldout"]:
                        blocked.append(f"{p['name']} 은(는) 품절입니다.")
                    elif p["stock"] is not None and p["stock"] < qty:
                        blocked.append(f"{p['name']} 재고가 부족합니다 (남은 수량 {p['stock']}개).")
                    else:
                        buyable.append((p, item, qty))
                if blocked:
                    raise OrderError("주문할 수 없는 상품이 있습니다.", blocked)

                quote = self.quote(cart)
                customer = self.repo.customer(customer_id)
                order_id = self._next_order_id()
                self.con.execute(
                    "insert into orders (order_id, customer_id, ordered_at, status, is_external_channel, "
                    "order_amount, shipping_fee, free_shipping_applied, address_region, invoice_printed) "
                    "values (?,?,?,?,0,?,?,?,?,0)",
                    (order_id, customer_id, now, "결제완료", quote["subtotal"], quote["shipping_fee"],
                     1 if quote["free_shipping_applied"] else 0,
                     customer["address_region"] if customer else None))
                for p, item, qty in buyable:
                    self.con.execute(
                        "insert into order_items (order_id, product_id, name, option, qty, price) "
                        "values (?,?,?,?,?,?)",
                        (order_id, p["product_id"], p["name"], item.get("option"), qty, p["price"]))
                    if p["stock"] is not None:
                        self.con.execute("update products set stock = stock - ? where product_id = ?",
                                         (qty, p["product_id"]))
                self.con.commit()
                return order_id
            except Exception:
                self.con.rollback()
                raise

    def orders_of(self, customer_id):
        return self._all(
            "select order_id, customer_id, ordered_at, status, status_detail, order_amount, shipping_fee, "
            "courier, tracking_no, expected_delivery, delivered_at from orders "
            "where customer_id = ? order by ordered_at desc, order_id desc", (customer_id,))

    def order_of(self, customer_id, order_id):
        with self._lock:
            o = _row(self.con.execute("select * from orders where order_id = ? and customer_id = ?",
                                       (order_id, customer_id)).fetchone())
        if not o:
            return None
        o["items"] = self._all(
            "select product_id, name, option, qty, price from order_items where order_id = ? order by id",
            (order_id,))
        return o
