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
from server.shoprepo import PAGE_SIZE, ShopRepo

TEMPLATES = Path(__file__).resolve().parent / "templates"
SESSION_COOKIE = "shop_session"
CART_COOKIE = "shop_cart"
COOKIE_PATH = "/shop"
SESSION_PURPOSE = "uid"


def _won(n):
    return "-" if n is None else f"{int(n):,}원"


def _mmdd(iso):
    return f"{int(iso[5:7])}월 {int(iso[8:10])}일" if iso else "-"


async def _urlencoded_form(request: Request) -> dict:
    """이 환경에는 python-multipart 가 없어 `request.form()`(starlette 1.6)이 urlencoded 본문마저
    못 읽는다(실측). 파일 업로드가 없는 로그인 폼이라 표준 라이브러리로 직접 읽는다."""
    body = await request.body()
    return dict(parse_qsl(body.decode("utf-8"), keep_blank_values=True))


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
    def product_list(request: Request, category: str = "", q: str = "", page: int = 1):
        shop = get_shop()
        rows, total = shop.products(category=category or None, q=q or None, page=page)
        return render(request, "products.html", current_customer(request), rows=rows, total=total, page=page,
                      pages=max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
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
        form = await _urlencoded_form(request)
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

    # ── 내 주문 (로그인 여부만 확인 — 목록/상세는 Task 5) ──
    @router.get("/orders", response_class=HTMLResponse)
    def my_orders(request: Request):
        customer = current_customer(request)
        if not customer:
            return redirect("/shop/login")
        return render(request, "orders.html", customer)

    return router
