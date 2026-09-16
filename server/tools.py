# -*- coding: utf-8 -*-
"""어드민 조회 도구. 매뉴얼의 [어드민 조회] 표시에서 도출한 것들이다.

주의 두 가지.
- 선택 인자는 반드시 Optional[...] 로 적는다. int 인데 기본값이 None 이면 모델이 None 을
  보냈을 때 스키마 검증에 걸려 왕복이 낭비된다.
- docstring 첫 줄이 모델이 읽는 도구 설명이다. 여기를 고치면 도구 선택이 달라진다.
"""
import difflib
import re
from typing import Callable, Optional

from server.domain import Domain

CAPITAL = ("서울", "경기", "인천", "수도권")


def clean(obj):
    """$ 로 시작하는 내부 주석 키를 재귀적으로 제거한다."""
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items() if not k.startswith("$")}
    if isinstance(obj, list):
        return [clean(v) for v in obj]
    return obj


def find_identifiers(text: str) -> dict:
    """발화에서 상품 ID(P0000)와 주문번호(O-0000)를 찾는다."""
    p = re.search(r"P\d{4}", text)
    o = re.search(r"O-\d{4}", text)
    return {"product_id": p.group(0) if p else None, "order_id": o.group(0) if o else None}


def _normalize_region(region: Optional[str]) -> Optional[str]:
    if not region:
        return None
    if any(k in region for k in ("제주", "도서", "산간", "울릉")):
        return "제주도서산간"
    if any(k in region for k in CAPITAL):
        return "수도권"
    return "수도권외"


def _toks(s: str) -> list[str]:
    return [t for t in re.split(r"[\s·()]+", s) if t]


def make_tools(domain: Domain) -> dict[str, Callable]:
    db = domain.mockdb
    products = {p["product_id"]: p for p in db["products"]}
    orders = {o["order_id"]: o for o in db["orders"]}
    returns = {r["return_id"]: r for r in db["returns"]}
    returns_by_order = {r["order_id"]: r for r in db["returns"]}
    restock = {r["product_id"]: r for r in db["restock"]}
    categories = db["categories"]
    same_day = db["same_day_delivery"]
    base_fee = domain.fixed_values["base_shipping_fee"]
    synonyms = domain.search["synonyms"]
    aliases = domain.search["aliases"]

    def _candidates_for_ids(ids, score=1.0):
        return [{"product_id": pid, "name": products[pid]["name"], "category": products[pid]["category"],
                 "price": products[pid]["price"], "score": score} for pid in ids]

    def search_product(query: str) -> dict:
        """상품명 일부로 상품을 찾는다. 상품 ID를 모를 때 가장 먼저 부르는 도구다.

        후보를 점수와 함께 돌려준다. 후보가 여럿이면(ambiguous) 확정하지 말고 고객에게 되물어야 한다.
        """
        qt = _toks(query)
        if not qt:
            return {"query": query, "candidates": [], "resolved_product_id": None, "ambiguous": False,
                    "category_query": False, "not_in_catalog": False}
        # 1) 동의어 치환. 값이 None 이면 취급하지 않는 범주다
        tokens = []
        for t in qt:
            if t in synonyms:
                if synonyms[t] is None:
                    return {"query": query, "candidates": [], "resolved_product_id": None,
                            "ambiguous": False, "category_query": False, "not_in_catalog": True,
                            "note": "취급하지 않는 상품입니다."}
                tokens.append(synonyms[t].replace(" ", ""))
            else:
                tokens.append(t)
        qt = tokens
        # 2) 범주어 사전. 문장 속 어디에 있어도 잡는다
        for t in qt:
            if t in aliases:
                ids = aliases[t]
                cands = _candidates_for_ids(ids)
                return {"query": query, "candidates": cands,
                        "resolved_product_id": ids[0] if len(ids) == 1 else None,
                        "ambiguous": len(ids) > 1, "category_query": True, "not_in_catalog": False,
                        "note": "범주 질의입니다. 후보 중 어느 상품인지 고객에게 확인하십시오." if len(ids) > 1 else None}
        flat_q = query.replace(" ", "")
        hits = []
        for pid, p in products.items():
            name = p["name"]
            flat = name.replace(" ", "")
            nt = _toks(name)
            overlap = sum(1 for t in qt if t in flat or any(t in x or x in t for x in nt))
            score = overlap / len(qt)
            if score == 0:
                # 오타 보정: 공백 제거 문자열 유사도
                ratio = difflib.SequenceMatcher(None, flat_q, flat).ratio()
                if ratio >= 0.6:
                    score = round(ratio * 0.9, 2)
            if score > 0:
                hits.append({"product_id": pid, "name": name, "category": p["category"],
                             "price": p["price"], "score": round(score, 2)})
        hits.sort(key=lambda h: -h["score"])
        top = [h for h in hits if h["score"] == hits[0]["score"]] if hits else []
        ambiguous = len(top) > 1
        return {"query": query, "candidates": hits[:5],
                "resolved_product_id": top[0]["product_id"] if len(top) == 1 else None,
                "ambiguous": ambiguous, "category_query": False,
                "not_in_catalog": not hits,
                "note": "후보가 여러 개입니다. 어느 상품인지 고객에게 확인하십시오." if ambiguous else None}

    def get_order_status(order_id: str) -> dict:
        """주문번호로 주문의 현재 진행 단계와 배송 정보를 조회한다."""
        o = orders.get(order_id)
        if not o:
            return {"error": "주문을 찾을 수 없습니다", "order_id": order_id}
        keys = ["order_id", "status", "status_detail", "is_external_channel", "items",
                "order_amount", "shipping_fee", "address_region", "courier", "tracking_no",
                "invoice_printed", "expected_ship_date"]
        return clean({k: o[k] for k in keys if k in o})

    def get_product_detail(product_id: str) -> dict:
        """상품 ID로 구성·소재·원산지·재고·보증서 동봉 여부를 조회한다."""
        p = products.get(product_id)
        if not p:
            return {"error": "상품을 찾을 수 없습니다", "product_id": product_id}
        keys = ["product_id", "name", "category", "price", "stock", "components", "material",
                "origin", "has_quality_cert", "made_to_order", "size_chart"]
        out = {k: p[k] for k in keys if k in p}
        out["category_label"] = categories[p["category"]]["label"]
        return clean(out)

    def get_product_options(product_id: str) -> dict:
        """상품 ID로 개별(단품) 구매 가능 여부와 판매 옵션을 조회한다."""
        p = products.get(product_id)
        if not p:
            return {"error": "상품을 찾을 수 없습니다", "product_id": product_id}
        keys = ["product_id", "name", "is_set", "components", "options",
                "individual_purchase_allowed", "individual_purchase_note", "made_to_order"]
        return clean({k: p[k] for k in keys if k in p})

    def _region_info(region: str) -> dict:
        key = _normalize_region(region)
        sd = clean(same_day.get(key, {}))
        return {"region_input": region, "region": key,
                "same_day_available": sd.get("available", False),
                "same_day_fee": sd.get("fee"),
                "extra_shipping_fee": sd.get("extra_fee"),
                "region_note": sd.get("note")}

    def get_shipping_policy(product_id: Optional[str] = None, category: Optional[str] = None,
                            order_amount: Optional[int] = None,
                            region: Optional[str] = None) -> dict:
        """무료배송 기준액과 배송비를 조회한다. 금액을 주면 부족액까지 계산한다.

        상품 ID 로도 카테고리로도 조회할 수 있다. region 을 주면 당일배송·도서산간 정보도 함께 준다.
        """
        p = products.get(product_id) if product_id else None
        if product_id and not p:
            return {"error": "상품을 찾을 수 없습니다", "product_id": product_id}
        cat_key = p["category"] if p else category
        if cat_key is None:
            out = {"base_shipping_fee": base_fee,
                   "note": "무료배송 기준은 카테고리마다 다릅니다. 상품 또는 카테고리를 지정해 주세요."}
            if region:
                out.update(_region_info(region))
            return clean(out)
        if cat_key not in categories:
            return {"error": "카테고리를 찾을 수 없습니다", "category": cat_key}
        cat = categories[cat_key]
        th = cat["free_shipping_threshold"]
        out = {"product_id": product_id, "name": p["name"] if p else None, "category": cat_key,
               "category_label": cat["label"], "price": p["price"] if p else None,
               "base_shipping_fee": base_fee, "free_shipping_threshold": th,
               "free_shipping_available": th is not None}
        if th is None:
            out["note"] = cat.get("free_shipping_note", "무료배송 대상이 아닙니다.")
        if order_amount is not None:
            out["order_amount"] = order_amount
            if th is not None and order_amount >= th:
                out["free_shipping_applied"] = True
                out["shipping_fee"] = 0
            else:
                out["free_shipping_applied"] = False
                out["shipping_fee"] = base_fee
                if th is not None:
                    out["shortfall"] = th - order_amount   # 부족액은 코드가 계산한다
        if region:
            out.update(_region_info(region))
        return clean(out)

    def get_return_policy(product_id: Optional[str] = None, order_id: Optional[str] = None) -> dict:
        """상품 또는 주문의 반품 가능 기간과 조건을 조회한다."""
        if order_id:
            o = orders.get(order_id)
            if not o:
                return {"error": "주문을 찾을 수 없습니다", "order_id": order_id}
            product_id = o["items"][0]["product_id"]
        p = products.get(product_id)
        if not p:
            return {"error": "상품을 찾을 수 없습니다", "product_id": product_id}
        cat = categories[p["category"]]
        mto = bool(p.get("made_to_order"))
        return clean({
            "product_id": product_id, "name": p["name"], "category": p["category"],
            "category_label": cat["label"],
            "return_window_days": cat["return_window_days"],
            "return_window_basis": cat["return_window_basis"],
            "requires_unopened": cat["requires_unopened"],
            "made_to_order": mto,
            "return_blocked": mto,
            "return_blocked_reason": "주문 제작 상품은 교환·반품이 불가합니다." if mto else None,
        })

    def get_return_status(order_id: Optional[str] = None, return_id: Optional[str] = None) -> dict:
        """반품·교환의 현재 처리 단계를 조회한다. 검품 전에는 귀책이 확정되지 않는다(null)."""
        r = returns.get(return_id) if return_id else returns_by_order.get(order_id)
        if not r:
            return {"error": "반품 접수 내역을 찾을 수 없습니다",
                    "order_id": order_id, "return_id": return_id}
        return clean(r)

    def get_restock_info(product_id: str) -> dict:
        """품절 상품의 재입고 확정 여부와 예정일을 조회한다. is_confirmed 가 false 면 예정일을 확답하지 않는다."""
        r = restock.get(product_id)
        if not r:
            p = products.get(product_id)
            if p and p.get("stock"):
                return {"product_id": product_id, "is_soldout": False, "stock": p["stock"],
                        "note": "재고가 있어 재입고 대기 상품이 아닙니다."}
            return {"error": "재입고 정보를 찾을 수 없습니다", "product_id": product_id}
        return clean(r)

    def escalate_to_agent(reason: str, context: Optional[dict] = None) -> dict:
        """상담원에게 이관한다. 매뉴얼 7.2의 이관 기준에 해당할 때 호출한다."""
        return {"escalated": True, "reason": reason, "context": context or {},
                "message": domain.escalate_message}

    return {f.__name__: f for f in [search_product, get_order_status, get_product_detail,
                                    get_product_options, get_shipping_policy, get_return_policy,
                                    get_return_status, get_restock_info, escalate_to_agent]}
