# -*- coding: utf-8 -*-
"""FastAPI 앱. 정적 화면을 서빙하고 통화 API를 제공한다."""
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from server.domain import Domain

WEB = Path(__file__).resolve().parent.parent / "web"


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


def create_app(pipeline, domain: Domain, tts=None) -> FastAPI:
    app = FastAPI(title=f"{domain.name} 음성 상담 에이전트")

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
        return {"call_id": call_id, "greeting": greeting, "customer": customer}

    @app.post("/api/call/end")
    def end_call(req: EndRequest):
        pipeline.end_call(req.call_id)
        return {"ok": True}

    @app.post("/api/call/turn")
    def turn(req: TurnRequest):
        try:
            return pipeline.turn(req.call_id, req.text).to_dict()
        except KeyError:  # Pipeline.turn에서 call_id 조회 실패만 처리
            raise HTTPException(status_code=404, detail="알 수 없는 call_id 입니다")

    @app.post("/api/tts")
    def synthesize(req: TtsRequest):
        # 서버 TTS 가 없으면 브라우저 음성으로 대체하라는 뜻으로 501
        if tts is None:
            raise HTTPException(status_code=501, detail="서버 TTS 가 설정되지 않았습니다")
        return Response(content=tts(req.text), media_type="audio/mpeg")

    @app.exception_handler(Exception)
    async def unhandled(request, exc):
        # 조용히 이관으로 바꾸지 않는다 — 원인이 그대로 보이게 500으로 드러낸다
        return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})

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
    return create_app(Pipeline(domain, settings), domain, tts=tts)
