# -*- coding: utf-8 -*-
"""FastAPI 앱. 정적 화면을 서빙하고 통화 API를 제공한다."""
import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from server.domain import Domain
from server.llmkey import current_request_key, redact
from server.pronounce import to_speech

logger = logging.getLogger(__name__)

WEB = Path(__file__).resolve().parent.parent / "web"

NO_KEY_MESSAGE = "설정에서 OpenAI 키를 입력해 주세요"


def _resolve_key(x_openai_key: Optional[str]) -> Optional[str]:
    """헤더 키가 있으면 그것을, 없으면 서버 환경변수를 폴백으로 쓴다. 둘 다 없으면 None."""
    return x_openai_key or os.environ.get("OPENAI_API_KEY") or None


class StartRequest(BaseModel):
    phone: Optional[str] = None


class EndRequest(BaseModel):
    call_id: str


class TtsRequest(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("text 가 비어 있습니다")
        return v.strip()[:1000]


class TurnRequest(BaseModel):
    call_id: str
    text: str

    @field_validator("text")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("text 가 비어 있습니다")
        return v.strip()


def create_app(pipeline, domain: Domain, tts=None, check_model: str = "gpt-4.1-mini") -> FastAPI:
    app = FastAPI(title=f"{domain.name} 음성 상담 에이전트")
    repo = getattr(pipeline, "repo", None)

    # 상품명 카탈로그(품번 "N번" 읽기용)는 요청마다 DB 를 훑지 않도록 한 번만 읽어 캐시한다.
    # None = 아직 안 읽음, list = 캐시된 상품명 목록(빈 리스트도 "읽었다"는 뜻).
    _product_names_cache: list[str] | None = None

    def _product_names() -> list[str]:
        nonlocal _product_names_cache
        if _product_names_cache is None:
            try:
                products = repo.products() if repo is not None else []
                _product_names_cache = [p["name"] for p in products if p.get("name")]
            except Exception:
                logger.warning("상품 카탈로그 조회 실패 — 품번 낭독 없이 진행합니다", exc_info=True)
                _product_names_cache = []
        return _product_names_cache

    def _safe_speech(text: str) -> str:
        """낭독용 문장 변환. 실패해도 통화가 끊기면 안 되므로 원문으로 폴백한다."""
        try:
            return to_speech(text, product_names=_product_names())
        except Exception:
            logger.warning("발음 변환 실패 — 원문을 그대로 사용합니다", exc_info=True)
            return text

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html")

    @app.get("/api/domain")
    def get_domain():
        sample_customers = pipeline.sample_customers() if hasattr(pipeline, "sample_customers") else []
        return {"name": domain.name, "greeting": domain.greeting, "tts_available": tts is not None,
                "sample_customers": sample_customers}

    @app.post("/api/call/start")
    def start_call(req: Optional[StartRequest] = None):
        phone = req.phone if req else None
        call_id, greeting, customer = pipeline.start_call(phone=phone)
        # profile 은 상담원 화면 전용(주소·전화·배송 이력). 프롬프트로 가는 customer 와 분리해 내려준다
        profile = None
        if customer and hasattr(pipeline, "customer_profile"):
            profile = pipeline.customer_profile(customer["customer_id"])
        return {"call_id": call_id, "greeting": greeting, "speech": _safe_speech(greeting),
                "customer": customer, "profile": profile}

    @app.post("/api/call/end")
    def end_call(req: EndRequest):
        pipeline.end_call(req.call_id)
        return {"ok": True}

    @app.post("/api/call/turn")
    def turn(req: TurnRequest, x_openai_key: Optional[str] = Header(None, alias="X-OpenAI-Key")):
        # 라우터·답변기가 실제로 OpenAI 를 부른다. 헤더 키도 서버 환경변수도 없으면 여기서
        # 바로 401 로 끊어, 화면이 모달을 열게 한다(파이프라인까지 들어가서 예외로 새지 않게).
        if _resolve_key(x_openai_key) is None:
            raise HTTPException(status_code=401, detail=NO_KEY_MESSAGE)
        try:
            result = pipeline.turn(req.call_id, req.text, api_key=x_openai_key).to_dict()
        except KeyError:  # Pipeline.turn에서 call_id 조회 실패만 처리
            raise HTTPException(status_code=404, detail="알 수 없는 call_id 입니다")
        result["speech"] = _safe_speech(result["answer"])
        return result

    @app.post("/api/tts")
    def synthesize(req: TtsRequest, x_openai_key: Optional[str] = Header(None, alias="X-OpenAI-Key")):
        # 서버 TTS 가 없으면 브라우저 음성으로 대체하라는 뜻으로 501 (키 확인보다 먼저 — 기존 동작 유지)
        if tts is None:
            raise HTTPException(status_code=501, detail="서버 TTS 가 설정되지 않았습니다")
        if _resolve_key(x_openai_key) is None:
            raise HTTPException(status_code=401, detail=NO_KEY_MESSAGE)
        return Response(content=tts(_safe_speech(req.text), api_key=x_openai_key), media_type="audio/mpeg")

    @app.post("/api/key/check")
    def check_key(x_openai_key: Optional[str] = Header(None, alias="X-OpenAI-Key")):
        """설정 모달의 "연결 확인" 버튼. 아주 짧은 모델 호출 1회로 키 유효성만 본다.
        키 원문이나 OpenAI 오류 원문은 절대 돌려주지 않는다 — 유효/무효 한 줄만."""
        if _resolve_key(x_openai_key) is None:
            raise HTTPException(status_code=401, detail=NO_KEY_MESSAGE)
        from server.llmkey import get_chat_model
        try:
            llm = get_chat_model(check_model, x_openai_key, cache=False)
            llm.invoke([("human", "ping")])
        except Exception:
            return {"ok": False, "message": "키가 올바르지 않습니다"}
        return {"ok": True, "message": "사용할 수 있는 키입니다"}

    @app.exception_handler(Exception)
    async def unhandled(request, exc):
        # 조용히 이관으로 바꾸지 않는다 — 원인이 그대로 보이게 500으로 드러낸다.
        # 다만 OpenAI 키가 예외 메시지에 섞여 나갈 수 있으므로(예: 인증 오류가 키 일부를
        # 되돌려주는 경우) redact 로 한 번 걸러낸다.
        #
        # redact() 의 정규식(_KEY_PATTERN)은 "sk-" 로 시작하지 않는 키나 공백이 섞인 키를
        # 놓친다(실측 확인). 이중 방어로 이 요청이 실어 온 키 원문 자체를 리터럴 치환
        # 대상으로 잠깐 등록해 둔다. base Exception 핸들러는 Starlette 가 ServerErrorMiddleware
        # (가장 바깥)에 연결하므로 요청 미들웨어의 try/finally 로는 이 시점까지 값이 남아
        # 있다고 보장할 수 없다 — 그래서 request 에서 직접 헤더를 읽어 여기서 바로 채운다.
        key = request.headers.get("x-openai-key")
        token = current_request_key.set(key) if key else None
        try:
            return JSONResponse(status_code=500, content={"detail": redact(f"{type(exc).__name__}: {exc}")})
        finally:
            if token is not None:
                current_request_key.reset(token)

    repo = getattr(pipeline, "repo", None)
    if repo is not None:
        from server.admin import admin_router
        app.include_router(admin_router(repo))

        from server.shop import shop_router
        app.include_router(shop_router(repo, domain))

    if WEB.exists():
        app.mount("/static", StaticFiles(directory=WEB), name="static")
    return app


def build_default_app() -> FastAPI:
    from server.config import load_settings
    from server.domain import load_domain
    from server.pipeline import Pipeline
    settings = load_settings()
    from server.tts import make_openai_tts
    domain = load_domain(settings.domains_root / settings.domain)
    tts = make_openai_tts(settings.tts_model, settings.tts_voice) if settings.tts_model else None
    return create_app(Pipeline(domain, settings), domain, tts=tts, check_model=settings.router_model)
