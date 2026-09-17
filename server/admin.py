# -*- coding: utf-8 -*-
"""어드민 조회 화면 라우터. 읽기 전용 — 쓰기 경로가 없다."""
import datetime
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from server.adminrepo import PAGE_SIZE, AdminRepo

TEMPLATES = Path(__file__).resolve().parent / "templates"


def _won(n):
    return "-" if n is None else f"{int(n):,}원"


def _mmdd(iso):
    return f"{int(iso[5:7])}월 {int(iso[8:10])}일" if iso else "-"


def _stamp(iso):
    return f"{int(iso[5:7])}월 {int(iso[8:10])}일 {iso[11:16]}" if iso else "-"


def admin_router(repo) -> APIRouter:
    admin = AdminRepo(repo.con)
    templates = Jinja2Templates(directory=str(TEMPLATES))
    templates.env.filters.update(won=_won, mmdd=_mmdd, stamp=_stamp)

    def page_url(path, query, page):
        q = {k: v for k, v in query.items() if k != "page" and v}
        q["page"] = page
        return f"{path}?{urlencode(q)}"

    templates.env.globals["page_url"] = page_url

    router = APIRouter(prefix="/admin")

    def render(request, name, **ctx):
        return templates.TemplateResponse(request, name, ctx)

    def listing(request, name, rows, total, page, **ctx):
        return render(request, name, rows=rows, total=total, page=page,
                      pages=max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
                      query=dict(request.query_params), path=request.url.path, **ctx)

    def not_found(request, what):
        return templates.TemplateResponse(request, "notfound.html", {"what": what}, status_code=404)

    @router.get("", response_class=HTMLResponse)
    def home(request: Request):
        s = admin.summary(datetime.datetime.now().isoformat())
        return render(request, "home.html", s=s)

    @router.get("/orders", response_class=HTMLResponse)
    def orders(request: Request, status: str = "", customer_id: str = "", to: str = "",
               page: int = Query(1, ge=1)):
        rows, total = admin.orders(status=status or None, customer_id=customer_id or None,
                                   date_from=request.query_params.get("from") or None,
                                   date_to=to or None, page=page)
        return listing(request, "orders.html", rows, total, page)

    @router.get("/returns", response_class=HTMLResponse)
    def returns(request: Request, stage: str = "", type: str = "", page: int = Query(1, ge=1)):
        rows, total = admin.returns(stage=stage or None, type=type or None, page=page)
        return listing(request, "returns.html", rows, total, page)

    @router.get("/calls", response_class=HTMLResponse)
    def calls(request: Request, page: int = Query(1, ge=1)):
        rows, total = admin.calls(page=page)
        return listing(request, "calls.html", rows, total, page)

    @router.get("/customers", response_class=HTMLResponse)
    def customers(request: Request, q: str = "", page: int = Query(1, ge=1)):
        rows, total = admin.customers(q=q or None, page=page)
        return listing(request, "customers.html", rows, total, page)

    @router.get("/orders/{order_id}", response_class=HTMLResponse)
    def order_detail(request: Request, order_id: str):
        o = admin.order_detail(order_id)
        return render(request, "order_detail.html", o=o) if o else not_found(request, f"주문 {order_id}")

    @router.get("/returns/{return_id}", response_class=HTMLResponse)
    def return_detail(request: Request, return_id: str):
        r = admin.return_detail(return_id)
        return render(request, "return_detail.html", r=r) if r else not_found(request, f"반품 {return_id}")

    @router.get("/calls/{call_id}", response_class=HTMLResponse)
    def call_detail(request: Request, call_id: str):
        c = admin.call_detail(call_id)
        return render(request, "call_detail.html", c=c) if c else not_found(request, f"통화 {call_id}")

    @router.get("/customers/{customer_id}", response_class=HTMLResponse)
    def customer_detail(request: Request, customer_id: str):
        d = admin.customer_detail(customer_id)
        return render(request, "customer_detail.html", d=d) if d else not_found(request, f"고객 {customer_id}")

    return router
