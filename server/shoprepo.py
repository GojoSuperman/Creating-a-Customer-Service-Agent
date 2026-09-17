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
        base = 1000 if row[0] is None else row[0]
        return f"O-{base + 1}"

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
        """SAVEPOINT 로 중첩 가능한 범위 안에서 주문·품목을 쓰고 재고를 줄인다.
        하나라도 살 수 없으면 전부 취소한다. 이미 열려 있는 외부(호출자) 트랜잭션은
        우리가 임의로 커밋·롤백하지 않는다 — 우리가 연 범위만 SAVEPOINT 로 되돌린다."""
        if not cart:
            raise OrderError("장바구니가 비어 있습니다.")
        for item in cart:
            if self._valid_qty(item.get("qty")) is None:
                raise OrderError("주문 수량이 올바르지 않습니다.",
                                  [f"{item.get('product_id')} 수량이 올바르지 않습니다."])
        now = now or datetime.datetime.now().isoformat()
        with self._lock:
            # 이미 열려 있는 트랜잭션(호출자 또는 이전 문장이 암묵적으로 연 것)이 있으면
            # 그것을 건드리지 않고 그 안에 SAVEPOINT 만 얹는다. 우리가 트랜잭션을 새로
            # 연 경우에만 우리가 커밋·롤백까지 책임진다.
            outermost = not self.con.in_transaction
            try:
                if outermost:
                    self.con.execute("begin immediate")
                self.con.execute("savepoint shop_order")
            except Exception:
                # begin/savepoint 자체가 실패해도 우리가 연 트랜잭션이면 누수 없이 정리한다.
                # 남의 트랜잭션(outermost=False)이면 아무것도 되돌리지 않고 그대로 전파한다.
                if outermost and self.con.in_transaction:
                    self.con.rollback()
                raise
            try:
                customer = self.repo.customer(customer_id)
                if customer is None:
                    raise OrderError("존재하지 않는 고객입니다.", [f"{customer_id} 고객을 찾을 수 없습니다."])

                # 같은 product_id 가 여러 줄로 나뉘어 담겼을 수 있으므로 줄 단위가 아니라
                # 상품별 합산 수량으로 재고를 검증한다(줄 단위 검증만 하면 각 줄은 통과해도
                # 합산은 재고를 넘어서 음수 재고가 될 수 있다).
                qty_by_product = {}
                for item in cart:
                    qty = self._valid_qty(item["qty"])
                    qty_by_product[item["product_id"]] = qty_by_product.get(item["product_id"], 0) + qty

                blocked, buyable = [], []
                for item in cart:
                    p = self.repo.product(item["product_id"])
                    qty = self._valid_qty(item["qty"])
                    total_qty = qty_by_product[item["product_id"]]
                    if p is None:
                        blocked.append(f"{item['product_id']} 상품을 찾을 수 없습니다.")
                    elif p["soldout"]:
                        blocked.append(f"{p['name']} 은(는) 품절입니다.")
                    elif p["stock"] is not None and p["stock"] < total_qty:
                        blocked.append(f"{p['name']} 재고가 부족합니다 (남은 수량 {p['stock']}개).")
                    else:
                        buyable.append((p, item, qty))
                if blocked:
                    raise OrderError("주문할 수 없는 상품이 있습니다.", blocked)

                quote = self.quote(cart)
                order_id = self._next_order_id()
                self.con.execute(
                    "insert into orders (order_id, customer_id, ordered_at, status, is_external_channel, "
                    "order_amount, shipping_fee, free_shipping_applied, address_region, invoice_printed) "
                    "values (?,?,?,?,0,?,?,?,?,0)",
                    (order_id, customer_id, now, "결제완료", quote["subtotal"], quote["shipping_fee"],
                     1 if quote["free_shipping_applied"] else 0, customer["address_region"]))
                for p, item, qty in buyable:
                    self.con.execute(
                        "insert into order_items (order_id, product_id, name, option, qty, price) "
                        "values (?,?,?,?,?,?)",
                        (order_id, p["product_id"], p["name"], item.get("option"), qty, p["price"]))
                    if p["stock"] is not None:
                        # 합산 검증을 이미 통과했더라도, 한 번 더 "차감 직전 재고 >= 이 줄의 수량"을
                        # 원자적으로 확인한다(이중 방어). rowcount 가 1 이 아니면 경합·모순 상태이므로
                        # 조용히 넘어가지 않고 전체를 취소한다.
                        cur = self.con.execute(
                            "update products set stock = stock - ? where product_id = ? and stock >= ?",
                            (qty, p["product_id"], qty))
                        if cur.rowcount != 1:
                            raise OrderError("주문할 수 없는 상품이 있습니다.",
                                              [f"{p['name']} 재고가 부족합니다."])

                self.con.execute("release shop_order")
                if outermost:
                    self.con.commit()
                return order_id
            except Exception:
                # 정리 문장 자체가 실패해도(예: 트랜잭션이 이미 끝나 "no such savepoint") 그
                # 정리 실패가 아니라 원래 예외를 그대로 전파해야 한다 — 각각 조용히 삼킨다.
                try:
                    self.con.execute("rollback to shop_order")
                except Exception:
                    pass
                try:
                    self.con.execute("release shop_order")
                except Exception:
                    pass
                if outermost:
                    try:
                        self.con.rollback()
                    except Exception:
                        pass
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
