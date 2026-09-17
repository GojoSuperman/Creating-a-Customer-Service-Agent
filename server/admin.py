# -*- coding: utf-8 -*-
"""어드민 조회 화면 라우터. 읽기 전용 — 쓰기 경로가 없다."""
import base64
import binascii
import datetime
import os
import secrets
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from server.adminrepo import PAGE_SIZE, AdminRepo

TEMPLATES = Path(__file__).resolve().parent / "templates"

_UNAUTHORIZED_HEADERS = {"WWW-Authenticate": 'Basic charset="UTF-8"'}


def _parse_basic_auth(header_value):
    """Authorization: Basic ... 헤더를 (username, password) 로 파싱한다.

    fastapi.security.HTTPBasic 의 기본 구현은 base64 디코드 결과를 ASCII 로만 디코드한다
    (`b64decode(param).decode("ascii")`) — 그래서 한글 등 non-ASCII 비밀번호는 올바르게
    보내도 이 단계에서 UnicodeDecodeError 로 걸러져 우리 검증 로직까지 오지도 못한 채
    401 이 된다(실측). RFC 7617 은 UTF-8 로 인코딩할 수 있다고 명시하므로, 여기서는
    HTTPBasic 대신 직접 파싱하며 UTF-8 로 디코드한다."""
    if not header_value:
        return None
    scheme, _, param = header_value.partition(" ")
    if scheme.lower() != "basic" or not param:
        return None
    try:
        decoded = base64.b64decode(param).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    username, sep, password = decoded.partition(":")
    if not sep:
        return None
    return username, password


def _admin_auth_dependency():
    """ADMIN_PASSWORD 환경변수가 설정돼 있으면 /admin/* 전체에 HTTP Basic 인증을 건다.

    fail-closed: ADMIN_PASSWORD 가 없거나 빈 문자열이면(환경변수 오타 등) 무인증으로 열어
    주는 대신 /admin/* 전체를 503 으로 막는다. 공개 배포에서 환경변수 설정을 빠뜨리면
    고객 개인정보(주소·전화·주문)가 그대로 노출되는 사고로 이어지기 때문이다.
    로컬 개발 편의는 명시적 옵트인(ALLOW_OPEN_ADMIN=1)으로만 허용한다.
    사용자명은 고정값 "admin" 을 쓴다."""
    password = os.environ.get("ADMIN_PASSWORD")
    if not password:
        if os.environ.get("ALLOW_OPEN_ADMIN") == "1":
            print("[admin] 경고: ADMIN_PASSWORD 가 설정되지 않아 ALLOW_OPEN_ADMIN=1 로 어드민 화면을 "
                  "인증 없이 엽니다. 로컬 개발 전용으로만 쓰세요.")
            return None

        def unavailable():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="어드민 비밀번호가 설정되지 않았습니다",
            )

        print("[admin] 경고: ADMIN_PASSWORD 가 설정되지 않아 /admin/* 을 503 으로 막습니다. "
              "로컬 개발 시 인증 없이 열려면 ALLOW_OPEN_ADMIN=1 을 설정하세요.")
        return unavailable

    def verify(request: Request):
        unauthorized = HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증 실패",
            headers=_UNAUTHORIZED_HEADERS,
        )
        creds = _parse_basic_auth(request.headers.get("authorization"))
        if creds is None:
            raise unauthorized
        username, given_password = creds
        # 한글 등 non-ASCII 비밀번호가 들어오면 secrets.compare_digest 가 str 인자에서
        # TypeError 를 던진다(실측: 정상 비번 401, 오답 500). 양쪽을 UTF-8 바이트로 인코딩해
        # 비교하고, 어떤 경우든 인증 실패는 항상 401 로 통일한다.
        user_ok = secrets.compare_digest(username.encode("utf-8"), b"admin")
        pass_ok = secrets.compare_digest(given_password.encode("utf-8"), password.encode("utf-8"))
        if not (user_ok and pass_ok):
            raise unauthorized
        return username

    return verify


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

    auth = _admin_auth_dependency()
    dependencies = [Depends(auth)] if auth else []
    router = APIRouter(prefix="/admin", dependencies=dependencies)

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
