# -*- coding: utf-8 -*-
"""쇼핑몰 고객 화면 저장소. 상품 조회와 금액 계산(배송비 포함)만 담당한다.
주문 생성은 Task 3, 라우트·화면은 Task 4~5 에서 다룬다."""
from server.repo import _row

PAGE_SIZE = 24


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
