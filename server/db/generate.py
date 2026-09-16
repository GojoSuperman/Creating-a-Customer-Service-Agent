# -*- coding: utf-8 -*-
"""도메인 폴더의 mockdb.json(정식 seed)을 그대로 넣고, 그 위에 실제 규모의 합성 데이터를 얹어 SQLite DB 를 만든다.

결정적(seed 고정)이라 언제 만들어도 같은 DB 가 나온다. 정식 seed 의 ID·값은 절대 바꾸지 않는다
(정답셋과 회귀 스위트가 그 값을 기준으로 채점하기 때문).
  python -m server.db.generate --domain modumall [--force]
"""
import argparse
import json
import random
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

SEED = 20260916
TODAY = date(2026, 9, 16)
SCHEMA = Path(__file__).with_name("schema.sql")

# ── 합성 데이터 재료 ────────────────────────────────────────────────────────
SURNAMES = "김이박최정강조윤장임한오서신권황안송류전홍고문양손배백허유남심노하곽성차주우구민진지엄채원천방공"
GIVEN = ["민준", "서준", "도윤", "예준", "시우", "하준", "지호", "주원", "지우", "서연", "서윤", "지민", "하은", "하윤", "민서",
         "수아", "지아", "윤서", "채원", "지유", "은우", "현우", "지훈", "준서", "유진", "다은", "소율", "예린", "수빈", "지원"]
REGIONS = [("수도권", ["서울특별시 강남구", "서울특별시 마포구", "경기도 성남시 분당구", "경기도 고양시", "인천광역시 연수구"], 0.62),
           ("수도권외", ["부산광역시 해운대구", "대구광역시 수성구", "대전광역시 유성구", "광주광역시 서구", "강원특별자치도 춘천시", "전북특별자치도 전주시"], 0.33),
           ("제주도서산간", ["제주특별자치도 제주시", "제주특별자치도 서귀포시", "경상남도 통영시 욕지면"], 0.05)]
NAMES = {
    "APPAREL": (["린넨 셔츠", "오버핏 후디", "슬랙스", "니트 가디건", "플리츠 스커트", "와이드 데님", "트렌치 코트", "패딩 조끼", "크롭 자켓", "롱 원피스",
                 "맨투맨", "카고 팬츠", "블라우스", "울 코트", "조거 팬츠", "폴로 셔츠"], ["베이직", "프리미엄", "라이트", "코튼", "울 블렌드", "스트레치"]),
    "UNDERWEAR": (["심리스 팬티 3매", "브라렛", "런닝 3매", "보정 속옷", "드로즈 2매", "잠옷 세트", "니플커버", "무봉제 브라"], ["코튼", "모달", "에어", "데일리", "소프트"]),
    "ACCESSORY": (["진주 귀걸이", "체인 팔찌", "레이어드 목걸이", "미니 반지", "헤어핀 세트", "실버 앵클릿", "볼 귀걸이", "커프 링"], ["실버", "14K 도금", "스틸", "미니멀", "빈티지"]),
    "BAG_GOODS": (["토트백", "백팩", "카드 지갑", "클러치", "버킷햇", "머플러", "벨트백", "에코백", "장지갑", "볼캡"], ["가죽", "캔버스", "나일론", "데님", "스웨이드"]),
    "SHOES": (["로퍼", "첼시 부츠", "러닝화", "샌들", "슬립온", "더비 슈즈", "하이탑", "메리제인", "슬리퍼", "워커"], ["클래식", "라이트", "쿠션", "스웨이드", "레더"]),
    "COSMETICS": (["수분 크림", "선크림", "클렌징 오일", "토너", "세럼", "립 틴트", "쿠션 팩트", "핸드크림", "바디로션", "마스크팩 10매"], ["어성초", "히알루론", "비타민", "시카", "티트리"]),
}
PRICE_RANGE = {"APPAREL": (19000, 189000), "UNDERWEAR": (9900, 49000), "ACCESSORY": (12000, 89000),
               "BAG_GOODS": (15000, 159000), "SHOES": (39000, 219000), "COSMETICS": (8900, 59000)}
SIZES = {"APPAREL": ["S", "M", "L", "XL"], "UNDERWEAR": ["S", "M", "L"], "SHOES": ["230", "240", "250", "260", "270"]}
COLORS = ["블랙", "화이트", "베이지", "네이비", "그레이", "브라운", "카키", "버건디"]
MATERIALS = {"APPAREL": ["면 100%", "면 80%, 폴리에스터 20%", "울 70%, 나일론 30%", "린넨 55%, 면 45%", "폴리에스터 100%"],
             "UNDERWEAR": ["면 95%, 폴리우레탄 5%", "모달 92%, 스판덱스 8%", "나일론 80%, 스판덱스 20%"],
             "ACCESSORY": ["925 실버", "황동 14K 도금", "서지컬 스틸"], "BAG_GOODS": ["소가죽", "캔버스", "나일론", "PU"],
             "SHOES": ["천연 가죽", "스웨이드", "메시 + 합성 피혁"], "COSMETICS": ["정제수, 글리세린, 부틸렌글라이콜 외"]}
ORIGINS = ["대한민국", "중국", "베트남", "이탈리아", "인도네시아"]
COURIERS = ["롯데택배"]
RETURN_REASONS = ["사이즈가 맞지 않음", "색상이 상세페이지와 다름", "단순 변심", "배송 중 파손", "오배송", "소재가 기대와 다름", "봉제 불량"]


def dt(d: date, h=0, m=0):
    return datetime(d.year, d.month, d.day, h, m).strftime("%Y-%m-%dT%H:%M:%S")


def jdump(v):
    return None if v is None else json.dumps(v, ensure_ascii=False)


def strip_notes(obj):
    if isinstance(obj, dict):
        return {k: strip_notes(v) for k, v in obj.items() if not k.startswith("$")}
    return obj


class Gen:
    def __init__(self, con: sqlite3.Connection, seed: dict, rnd: random.Random):
        self.con, self.seed, self.rnd = con, seed, rnd
        self.products: dict[str, dict] = {}
        self.customers: list[dict] = []
        self.categories = {k: strip_notes(v) for k, v in seed["categories"].items()}

    # ── 참조 데이터 ─────────────────────────────────────────────
    def refs(self):
        for k, c in self.categories.items():
            self.con.execute("insert into categories values (?,?,?,?,?,?,?)",
                             (k, c["label"], c.get("free_shipping_threshold"), c.get("free_shipping_note"),
                              c["return_window_days"], c["return_window_basis"], int(c["requires_unopened"])))
        for region, s in strip_notes(self.seed["same_day_delivery"]).items():
            self.con.execute("insert into same_day_delivery values (?,?,?,?,?)",
                             (region, int(s.get("available", False)), s.get("fee"), s.get("extra_fee"), s.get("note")))

    # ── 정식 seed 그대로 ────────────────────────────────────────
    def canonical(self):
        for p in self.seed["products"]:
            self.insert_product(strip_notes(p))
        # 정식 주문 11건에 고객 8명 배정 (결정적 순서)
        owners = [self.new_customer() for _ in range(8)]
        for i, o in enumerate(self.seed["orders"]):
            o = strip_notes(o)
            cust = owners[i % 8]
            self.insert_order(o, cust["customer_id"])
        for r in self.seed["returns"]:
            self.insert_return(strip_notes(r))
        for r in self.seed["restock"]:
            r = strip_notes(r)
            self.con.execute("insert into restock values (?,?,?,?,?,?,?)",
                             (r["product_id"], r["name"], int(r["is_soldout"]), int(r["is_confirmed"]),
                              r.get("expected_date"), r.get("expected_note"), int(r.get("notify_available", True))))

    def insert_product(self, p: dict):
        self.products[p["product_id"]] = p
        self.con.execute("""insert into products (product_id,name,category,price,stock,is_set,components,material,material_note,origin,
            has_quality_cert,made_to_order,made_to_order_days,options,size_chart,size_matching,individual_purchase_allowed,
            individual_purchase_note,individual_prices,return_allowed,return_blocked_reason,soldout,soldout_note,stock_note)
            values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (p["product_id"], p["name"], p["category"], p["price"], p.get("stock"), int(p.get("is_set", False)),
             jdump(p.get("components")), p.get("material"), p.get("material_note"), p.get("origin"),
             int(p.get("has_quality_cert", False)), int(p.get("made_to_order", False)),
             jdump(p["made_to_order_days"]) if isinstance(p.get("made_to_order_days"), (list, dict)) else p.get("made_to_order_days"),
             jdump(p.get("options")), jdump(p.get("size_chart")), jdump(p.get("size_matching")),
             None if p.get("individual_purchase_allowed") is None else int(p["individual_purchase_allowed"]),
             p.get("individual_purchase_note"), jdump(p.get("individual_prices")),
             None if p.get("return_allowed") is None else int(p["return_allowed"]), p.get("return_blocked_reason"),
             int(p.get("soldout", False)), p.get("soldout_note"), p.get("stock_note")))

    def insert_order(self, o: dict, customer_id):
        self.con.execute("""insert into orders (order_id,customer_id,ordered_at,status,status_detail,is_external_channel,external_channel_name,
            order_amount,shipping_fee,free_shipping_applied,address_region,courier,tracking_no,invoice_printed,expected_ship_date,
            shipped_at,expected_delivery,delivered_at,delay_days,delay_reason,return_id,note) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (o["order_id"], customer_id, o["ordered_at"], o["status"], o.get("status_detail"), int(o.get("is_external_channel") or False),
             o.get("external_channel_name"), o["order_amount"], o.get("shipping_fee") or 0, int(o.get("free_shipping_applied") or False),
             o.get("address_region"), o.get("courier"), o.get("tracking_no"), int(o.get("invoice_printed", False)),
             o.get("expected_ship_date"), o.get("shipped_at"), o.get("expected_delivery"), o.get("delivered_at"),
             o.get("delay_days"), o.get("delay_reason"), o.get("return_id"), o.get("note")))
        for it in o["items"]:
            self.con.execute("insert into order_items (order_id,product_id,name,option,qty,price) values (?,?,?,?,?,?)",
                             (o["order_id"], it["product_id"], it["name"], it.get("option"), it["qty"], it["price"]))
        for ev in self.events_for(o):
            self.con.execute("insert into shipment_events (order_id,at,status,location) values (?,?,?,?)", (o["order_id"], *ev))

    def events_for(self, o: dict):
        """주문 상태에 맞는 배송 이력을 만든다 (정식 주문에도 붙인다 — 값 충돌 없음)."""
        evs = [(o["ordered_at"], "주문 접수", "온라인")]
        if o.get("shipped_at"):
            evs.append((o["shipped_at"], "출고", "모두몰 물류센터"))
            evs.append((o["shipped_at"][:10] + "T21:00:00", "간선 상차", "옥천 HUB"))
        if o.get("delivered_at"):
            evs.append((o["delivered_at"], "배송 완료", o.get("address_region") or ""))
        return evs

    def insert_return(self, r: dict):
        self.con.execute("""insert into returns (return_id,order_id,type,return_scope,reason_stated,requested_at,stage,inspection_result,
            fault_party,shipping_fee_bearer,return_fee,refund_amount,refund_calc,expected_completion,exchange_target,exchange_available,
            exchange_blocked_reason,convert_to_refund,courier_visit_expected,note) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (r["return_id"], r["order_id"], r["type"], r.get("return_scope"), r.get("reason_stated"), r["requested_at"], r["stage"],
             r.get("inspection_result"), r.get("fault_party"), r.get("shipping_fee_bearer"), r.get("return_fee"), r.get("refund_amount"),
             r.get("refund_calc"), r.get("expected_completion"), jdump(r.get("exchange_target")),
             None if r.get("exchange_available") is None else int(r["exchange_available"]), r.get("exchange_blocked_reason"),
             None if r.get("convert_to_refund") is None else int(r["convert_to_refund"]), r.get("courier_visit_expected"), r.get("note")))
        for h in r.get("stage_history", []):
            self.con.execute("insert into return_stage_history (return_id,stage,date) values (?,?,?)", (r["return_id"], h["stage"], h["date"]))

    # ── 합성 ───────────────────────────────────────────────────
    def new_customer(self):
        n = len(self.customers) + 1
        region, addrs, _ = self.rnd.choices(REGIONS, weights=[w for _, _, w in REGIONS])[0]
        c = {"customer_id": f"C-{n:04d}", "name": self.rnd.choice(SURNAMES) + self.rnd.choice(GIVEN),
             "phone": f"010-{self.rnd.randint(2000, 9999):04d}-{n:04d}", "address_region": region,
             "address": f"{self.rnd.choice(addrs)} {self.rnd.randint(1, 99)}길 {self.rnd.randint(1, 60)}",
             "joined_at": (TODAY - timedelta(days=self.rnd.randint(30, 900))).isoformat()}
        self.customers.append(c)
        self.con.execute("insert into customers values (?,?,?,?,?,?)", tuple(c.values()))
        return c

    def synth_products(self, total=180):
        # 정식 상품 다음 번호부터 (P1004, P2004, …). 카테고리 접두 숫자는 mockdb 규칙을 따른다
        prefix = {"UNDERWEAR": 1, "ACCESSORY": 2, "APPAREL": 3, "SHOES": 4, "COSMETICS": 5, "BAG_GOODS": 6}
        existing = {k: max((int(pid[2:]) for pid in self.products if int(pid[1]) == prefix[k]), default=0) for k in prefix}
        cats = list(NAMES)
        while len(self.products) < total:
            cat = cats[len(self.products) % len(cats)]
            base, mods = NAMES[cat]
            name = f"{self.rnd.choice(mods)} {self.rnd.choice(base)}"
            if any(p["name"] == name for p in self.products.values()):
                name += f" {self.rnd.choice(['II', '라인', '에디션', '컬렉션'])}"
            existing[cat] += 1
            pid = f"P{prefix[cat]}{existing[cat]:03d}"
            lo, hi = PRICE_RANGE[cat]
            price = int(round(self.rnd.randint(lo, hi) / 100) * 100)
            soldout = self.rnd.random() < 0.06
            sizes = SIZES.get(cat)
            options = {"color": self.rnd.sample(COLORS, self.rnd.randint(1, 3))}
            if sizes:
                options["size"] = sizes
            size_chart = {s: f"{'발길이' if cat == 'SHOES' else '가슴'} {self.rnd.randint(60, 120)}cm" for s in sizes} if sizes else None
            mto = cat == "APPAREL" and self.rnd.random() < 0.05
            p = {"product_id": pid, "name": name, "category": cat, "price": price, "stock": 0 if soldout else self.rnd.randint(3, 300),
                 "is_set": "세트" in name or "매" in name, "components": None, "material": self.rnd.choice(MATERIALS[cat]),
                 "origin": self.rnd.choice(ORIGINS), "has_quality_cert": cat == "ACCESSORY" and self.rnd.random() < 0.6,
                 "made_to_order": mto, "made_to_order_days": 14 if mto else None, "options": options, "size_chart": size_chart,
                 "individual_purchase_allowed": (not ("세트" in name or "매" in name)) or self.rnd.random() < 0.3,
                 "return_allowed": not mto, "return_blocked_reason": "주문 제작 상품은 교환·반품이 불가합니다." if mto else None,
                 "soldout": soldout, "soldout_note": "일시 품절" if soldout else None}
            self.insert_product(p)

    def synth_customers(self, total=120):
        while len(self.customers) < total:
            self.new_customer()

    def synth_orders(self, total=450):
        n = 1011
        pids = [p for p in self.products.values() if not p.get("soldout")]
        returns_pending = []
        while n < 1000 + total:
            n += 1
            oid = f"O-{n}"
            cust = self.rnd.choice(self.customers)
            d = TODAY - timedelta(days=self.rnd.randint(0, 89))
            ordered = dt(d, self.rnd.randint(8, 22), self.rnd.randint(0, 59))
            items = []
            for p in self.rnd.sample(pids, self.rnd.randint(1, 3)):
                opt = " / ".join(x for x in [self.rnd.choice(p["options"].get("size") or [""]),
                                             self.rnd.choice(p["options"].get("color") or [""])] if x)
                items.append({"product_id": p["product_id"], "name": p["name"], "option": opt or None,
                              "qty": self.rnd.choices([1, 2, 3], weights=[80, 15, 5])[0], "price": p["price"]})
            amount = sum(i["qty"] * i["price"] for i in items)
            cats = {self.products[i["product_id"]]["category"] for i in items}
            ths = [self.categories[c].get("free_shipping_threshold") for c in cats]
            free = all(t is not None for t in ths) and amount >= max(ths)
            fee = 0 if free else 2500
            extra = 3000 if cust["address_region"] == "제주도서산간" else 0
            age = (TODAY - d).days
            r = self.rnd.random()
            status, detail = "배송완료", "수령 완료"
            shipped = delivered = tracking = None
            invoice = True
            exp_ship = (d + timedelta(days=1 if int(ordered[11:13]) < 11 else 2)).isoformat()
            if age <= 1 or r < 0.10:
                status, detail, invoice = "결제완료", "출고 대기", False
            elif age <= 4 or r < 0.25:
                status, detail = "배송중", "간선 상차" if self.rnd.random() < 0.7 else "배송 지연"
                shipped = dt(date.fromisoformat(exp_ship), 14)
                tracking = f"{self.rnd.randint(10**11, 10**12 - 1)}"
            else:
                shipped = dt(date.fromisoformat(exp_ship), 14)
                delivered = dt(date.fromisoformat(exp_ship) + timedelta(days=1 if cust["address_region"] == "수도권" else 3), 16)
                tracking = f"{self.rnd.randint(10**11, 10**12 - 1)}"
                if r > 0.88:
                    status, detail = ("반품진행", "접수") if r < 0.96 else ("교환진행", "접수")
                    returns_pending.append((oid, status, items, amount, delivered))
            delay = 2 if detail == "배송 지연" else None
            o = {"order_id": oid, "ordered_at": ordered, "status": status, "status_detail": detail, "is_external_channel": False,
                 "items": items, "order_amount": amount, "shipping_fee": fee + extra, "free_shipping_applied": free,
                 "address_region": cust["address_region"], "courier": COURIERS[0] if shipped else None, "tracking_no": tracking,
                 "invoice_printed": invoice, "expected_ship_date": exp_ship, "shipped_at": shipped,
                 "expected_delivery": None if not shipped else (date.fromisoformat(shipped[:10]) + timedelta(days=2)).isoformat(),
                 "delivered_at": delivered, "delay_days": delay, "delay_reason": "물량 증가로 인한 간선 지연" if delay else None}
            self.insert_order(o, cust["customer_id"])
        self.synth_returns(returns_pending)

    def synth_returns(self, pending):
        n = 2005
        for oid, status, items, amount, delivered in pending:
            n += 1
            rid = f"R-{n}"
            req = date.fromisoformat(delivered[:10]) + timedelta(days=self.rnd.randint(1, 6))
            stages = ["접수", "수거대기", "수거완료", "입고완료", "검품중", "승인", "환불완료"]
            upto = self.rnd.randint(1, 6)
            hist = [{"stage": s, "date": (req + timedelta(days=i)).isoformat()} for i, s in enumerate(stages[:upto + 1])]
            stage = hist[-1]["stage"]
            inspected = stage in ("승인", "환불완료")
            fault = self.rnd.choice(["판매자", "고객"]) if inspected else None
            reason = self.rnd.choice(RETURN_REASONS)
            r = {"return_id": rid, "order_id": oid, "type": "교환" if status == "교환진행" else "반품", "return_scope": "전체",
                 "reason_stated": reason, "requested_at": req.isoformat(), "stage": stage,
                 "inspection_result": (("하자" if fault == "판매자" else "정상") if inspected else None),
                 "fault_party": fault, "shipping_fee_bearer": (("판매자" if fault == "판매자" else "고객") if inspected else None),
                 "return_fee": (0 if fault == "판매자" else 5000) if inspected else None,
                 "refund_amount": (amount if fault == "판매자" else amount - 5000) if inspected else None,
                 "expected_completion": (req + timedelta(days=7)).isoformat(), "stage_history": hist,
                 "note": None if inspected else "검품 미완료로 귀책 미확정"}
            self.insert_return(r)
            self.con.execute("update orders set return_id=? where order_id=?", (rid, oid))

    def synth_restock(self, total=8):
        sold = [p for p in self.products.values() if p.get("soldout") and p["product_id"] not in {r["product_id"] for r in self.seed["restock"]}]
        for p in sold[: total - len(self.seed["restock"])]:
            confirmed = self.rnd.random() < 0.5
            exp = (TODAY + timedelta(days=self.rnd.randint(3, 21))).isoformat() if confirmed else None
            self.con.execute("insert into restock values (?,?,?,?,?,?,?)",
                             (p["product_id"], p["name"], 1, int(confirmed), exp,
                              f"{exp[5:7].lstrip('0')}월 {exp[8:10].lstrip('0')}일 입고 예정입니다." if confirmed else "재입고 일정이 확정되지 않았습니다. 알림 신청 시 입고 즉시 연락드립니다.", 1))


def generate(domain_dir: Path, force: bool = False) -> Path:
    domain_dir = Path(domain_dir)
    db_path = domain_dir / f"{domain_dir.name}.db"
    if db_path.exists() and not force:
        return db_path
    seed = json.loads((domain_dir / "mockdb.json").read_text(encoding="utf-8"))
    tmp = db_path.with_suffix(".db.tmp")
    for p in (tmp, tmp.with_suffix(".db.tmp-wal"), tmp.with_suffix(".db.tmp-shm")):
        if p.exists():
            p.unlink()
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA.read_text(encoding="utf-8"))
    g = Gen(con, seed, random.Random(SEED))
    g.refs(); g.canonical(); g.synth_customers(); g.synth_products(); g.synth_orders(); g.synth_restock()
    con.commit(); con.close()
    if db_path.exists():
        db_path.unlink()
    tmp.rename(db_path)
    for p in (db_path.with_suffix(".db-wal"), db_path.with_suffix(".db-shm")):
        if p.exists():
            p.unlink()
    return db_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default="modumall")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    root = Path(__file__).resolve().parents[2] / "domains" / a.domain
    print("생성:", generate(root, force=a.force))
