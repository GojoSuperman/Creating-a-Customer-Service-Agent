# -*- coding: utf-8 -*-
"""쇼핑몰 고객 화면 라우터. 주문 쓰기는 ShopRepo 에만 있다."""
import itertools
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from server import shopcookie
from server.repo import normalize_phone
from server.shoprepo import PAGE_SIZE, OrderError, ShopRepo

TEMPLATES = Path(__file__).resolve().parent / "templates"
SESSION_COOKIE = "shop_session"
CART_COOKIE = "shop_cart"
COOKIE_PATH = "/shop"
SESSION_PURPOSE = "uid"
MAX_FORM_BODY_BYTES = 64 * 1024  # 로그인 폼 등 urlencoded 본문 상한 — 익명이 두드릴 수 있는 엔드포인트라 메모리 낭비를 막는다


def _won(n):
    return "-" if n is None else f"{int(n):,}원"


def _mmdd(iso):
    return f"{int(iso[5:7])}월 {int(iso[8:10])}일" if iso else "-"


class FormTooLarge(Exception):
    """urlencoded 본문이 MAX_FORM_BODY_BYTES 를 넘었다."""


async def _read_capped_body(request: Request, limit: int) -> bytes:
    """request.stream() 을 청크 단위로 읽다가 limit 을 넘는 순간 멈춘다 — 큰 본문을 전부 메모리에
    올린 뒤에야 거부하지 않기 위해서다."""
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise FormTooLarge()
        chunks.append(chunk)
    return b"".join(chunks)


async def _urlencoded_form(request: Request) -> dict:
    """이 환경에는 python-multipart 가 없어 `request.form()`(starlette 1.6)이 urlencoded 본문마저
    못 읽는다(실측). 파일 업로드가 없는 로그인 폼이라 표준 라이브러리로 직접 읽는다.

    누구나 두드릴 수 있는 엔드포인트라 (1) 본문 크기를 MAX_FORM_BODY_BYTES 로 제한하고
    (2) 비 UTF-8 바이트가 섞여도 500 대신 손실 허용 디코드로 넘어간다(실측: 원시 바이트를 보내면
    UnicodeDecodeError 로 미처리 예외가 터졌었다)."""
    body = await _read_capped_body(request, MAX_FORM_BODY_BYTES)
    return dict(parse_qsl(body.decode("utf-8", "replace"), keep_blank_values=True))


def _option_combos(options):
    """{"size": [...], "color": [...]} 형태를 "S / 파스텔" 같은 조합 문자열 목록으로 편다."""
    if not options:
        return []
    keys = list(options.keys())
    values = [options[k] for k in keys]
    return [" / ".join(combo) for combo in itertools.product(*values)]


def shop_router(repo, domain) -> APIRouter:
    # ShopRepo 생성은 요청이 실제로 들어올 때까지 미룬다 — 다른 라우터(예: 어드민) 테스트가 쓰는
    # 최소 기능 가짜 repo 는 ShopRepo 가 요구하는 속성(_lock 등)을 갖추지 않았을 수 있다.
    _shop_holder = {}

    def get_shop():
        if "shop" not in _shop_holder:
            _shop_holder["shop"] = ShopRepo(repo, domain)
        return _shop_holder["shop"]

    templates = Jinja2Templates(directory=str(TEMPLATES))
    templates.env.filters.update(won=_won, mmdd=_mmdd)

    def page_url(path, query, page):
        q = {k: v for k, v in query.items() if k != "page" and v}
        q["page"] = page
        return f"{path}?{urlencode(q)}"

    templates.env.globals["page_url"] = page_url
    router = APIRouter(prefix="/shop")

    # ── 세션·장바구니 ───────────────────────────────────
    def current_customer(request: Request):
        cid = shopcookie.unsign(request.cookies.get(SESSION_COOKIE), SESSION_PURPOSE)
        return repo.customer(cid) if cid else None

    def cart_of(request: Request):
        return shopcookie.load_cart(request.cookies.get(CART_COOKIE))

    def set_cart(response, cart):
        response.set_cookie(CART_COOKIE, shopcookie.dump_cart(cart), httponly=True, samesite="lax",
                            path=COOKIE_PATH)

    def render(request, name, customer, cart_count=None, **ctx):
        if cart_count is None:
            cart_count = len(cart_of(request))
        return templates.TemplateResponse(request, f"shop/{name}",
                                          {"customer": customer, "cart_count": cart_count, **ctx})

    def not_found(request, what, customer):
        return templates.TemplateResponse(request, "shop/notfound.html",
                                          {"what": what, "customer": customer, "cart_count": len(cart_of(request))},
                                          status_code=404)

    def redirect(path):
        return RedirectResponse(path, status_code=303)

    def sample_customers():
        """데모용 예시 고객 3명 (이름·전화). Repo 에 전용 메서드가 없어 직접 조회한다."""
        shop = get_shop()
        with shop._lock:
            rows = shop.con.execute("select name, phone from customers order by customer_id limit 3").fetchall()
        return [{"name": r[0], "phone": r[1]} for r in rows]

    # ── 상품 ────────────────────────────────────────────
    @router.get("", response_class=HTMLResponse)
    def product_list(request: Request, category: str = "", q: str = "", page: str = "1"):
        # HTML 화면은 422 JSON 대신 잘못된 값을 1로 눙친다 (page=abc 실측 — 지금은 422 JSON).
        try:
            page_num = max(1, int(page))
        except (TypeError, ValueError):
            page_num = 1
        shop = get_shop()
        rows, total = shop.products(category=category or None, q=q or None, page=page_num)
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        if page_num > pages:  # page=999 처럼 범위 밖이면 마지막 페이지로 당긴다
            page_num = pages
            rows, total = shop.products(category=category or None, q=q or None, page=page_num)
        return render(request, "products.html", current_customer(request), rows=rows, total=total, page=page_num,
                      pages=pages,
                      categories=shop.category_list(), query=dict(request.query_params), path=request.url.path)

    @router.get("/products/{product_id}", response_class=HTMLResponse)
    def product_detail(request: Request, product_id: str):
        customer = current_customer(request)
        p = get_shop().product(product_id)
        if not p:
            return not_found(request, f"상품 {product_id}", customer)
        return render(request, "product_detail.html", customer, p=p, option_combos=_option_combos(p.get("options")))

    # ── 로그인 ──────────────────────────────────────────
    @router.get("/login", response_class=HTMLResponse)
    def login_form(request: Request):
        return render(request, "login.html", current_customer(request), error=None, samples=sample_customers())

    @router.post("/login")
    async def login(request: Request):
        try:
            form = await _urlencoded_form(request)
        except FormTooLarge:
            return HTMLResponse("요청 본문이 너무 큽니다.", status_code=413)
        phone = (form.get("phone") or "").strip()
        c = repo.customer_by_phone(normalize_phone(phone)) if phone else None
        if not c:
            return render(request, "login.html", None, error="가입 이력이 없는 번호입니다.",
                          samples=sample_customers())
        response = redirect("/shop")
        response.set_cookie(SESSION_COOKIE, shopcookie.sign(str(c["customer_id"]), SESSION_PURPOSE),
                            httponly=True, samesite="lax", path=COOKIE_PATH)
        return response

    @router.post("/logout")
    def logout():
        response = redirect("/shop")
        response.delete_cookie(SESSION_COOKIE, path=COOKIE_PATH)
        return response

    def _missing_lines(shop, cart):
        """더 이상 존재하지 않는(삭제된) 상품이 담긴 줄. quote() 는 이런 줄을 조용히 건너뛰므로
        화면에 따로 보여주지 않으면 사용자가 뺄 방법이 없는 막다른 장바구니가 된다."""
        return [line for line in cart if shop.product(line["product_id"]) is None]

    # ── 장바구니 ────────────────────────────────────────
    @router.get("/cart", response_class=HTMLResponse)
    def cart_view(request: Request):
        cart = cart_of(request)
        shop = get_shop()
        return render(request, "cart.html", current_customer(request), quote=shop.quote(cart), blocked=[],
                      missing=_missing_lines(shop, cart))

    @router.post("/cart/add")
    async def cart_add(request: Request):
        try:
            form = await _urlencoded_form(request)
        except FormTooLarge:
            return HTMLResponse("요청 본문이 너무 큽니다.", status_code=413)
        product_id = (form.get("product_id") or "")[:shopcookie.MAX_PRODUCT_ID_LEN]
        option = (form.get("option") or "")[:shopcookie.MAX_OPTION_LEN]
        try:
            qty = int(form.get("qty") or 1)
        except ValueError:
            qty = 1
        qty = max(1, min(shopcookie.MAX_QTY, qty))
        cart = cart_of(request)
        for line in cart:
            if line["product_id"] == product_id and (line["option"] or "") == option:
                line["qty"] = min(shopcookie.MAX_QTY, line["qty"] + qty)
                break
        else:
            cart.append({"product_id": product_id, "option": option or None, "qty": qty})
        response = redirect("/shop/cart")
        set_cart(response, cart[:shopcookie.MAX_CART_LINES])
        return response

    @router.post("/cart/update")
    async def cart_update(request: Request):
        try:
            form = await _urlencoded_form(request)
        except FormTooLarge:
            return HTMLResponse("요청 본문이 너무 큽니다.", status_code=413)
        product_id = form.get("product_id") or ""
        # option 필드가 아예 안 왔으면(기존 화면처럼 상품당 한 줄뿐인 경우) product_id 만으로 찾는다.
        # option 필드가 왔으면(같은 상품이 옵션별로 여러 줄일 때) 그 옵션의 줄만 골라낸다.
        option_given = "option" in form
        option = (form.get("option") or "") or None
        try:
            qty = int(form.get("qty") or 0)
        except ValueError:
            qty = 0
        cart = []
        for line in cart_of(request):
            matches = line["product_id"] == product_id and (not option_given or (line["option"] or None) == option)
            if matches:
                if qty > 0:
                    line["qty"] = max(1, min(shopcookie.MAX_QTY, qty))
                    cart.append(line)
                # qty <= 0 이면 이 줄만 삭제하고 건너뛴다
            else:
                cart.append(line)
        response = redirect("/shop/cart")
        set_cart(response, cart)
        return response

    # ── 주문 ────────────────────────────────────────────
    @router.get("/checkout", response_class=HTMLResponse)
    def checkout_form(request: Request):
        customer = current_customer(request)
        if not customer:
            return redirect("/shop/login")
        cart = cart_of(request)
        if not cart:
            return redirect("/shop/cart")
        shop = get_shop()
        return render(request, "checkout.html", customer, quote=shop.quote(cart))

    @router.post("/checkout")
    def checkout(request: Request):
        customer = current_customer(request)
        if not customer:
            return redirect("/shop/login")
        cart = cart_of(request)
        shop = get_shop()
        try:
            order_id = shop.create_order(customer["customer_id"], cart)
        except OrderError as e:
            reasons = [e.message, *e.blocked]
            deduped = list(dict.fromkeys(reasons))  # 같은 상품이 여러 줄이면 같은 사유가 반복되므로 순서를 지키며 중복 제거
            return render(request, "cart.html", customer, quote=shop.quote(cart), blocked=deduped,
                          missing=_missing_lines(shop, cart))
        response = redirect(f"/shop/orders/{order_id}?new=1")
        set_cart(response, [])                    # 주문이 끝나면 장바구니를 비운다
        return response

    @router.get("/orders", response_class=HTMLResponse)
    def my_orders(request: Request):
        customer = current_customer(request)
        if not customer:
            return redirect("/shop/login")
        return render(request, "orders.html", customer, rows=get_shop().orders_of(customer["customer_id"]))

    @router.get("/orders/{order_id}", response_class=HTMLResponse)
    def my_order_detail(request: Request, order_id: str, new: str = ""):
        customer = current_customer(request)
        if not customer:
            return redirect("/shop/login")
        o = get_shop().order_of(customer["customer_id"], order_id)
        if not o:
            return not_found(request, f"주문 {order_id}", customer)
        return render(request, "order_detail.html", customer, o=o, just_ordered=bool(new))

    return router
