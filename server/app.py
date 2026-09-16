# -*- coding: utf-8 -*-
"""FastAPI 앱. 정적 화면을 서빙하고 통화 API 3개를 제공한다."""
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from server.domain import Domain

WEB = Path(__file__).resolve().parent.parent / "web"


class TurnRequest(BaseModel):
    call_id: str
    text: str

    @field_validator("text")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("text 가 비어 있습니다")
        return v.strip()


def create_app(pipeline, domain: Domain) -> FastAPI:
    app = FastAPI(title=f"{domain.name} 음성 상담 에이전트")

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html")

    @app.get("/api/domain")
    def get_domain():
        return {"name": domain.name, "greeting": domain.greeting}

    @app.post("/api/call/start")
    def start_call():
        return {"call_id": pipeline.start_call(), "greeting": domain.greeting}

    @app.post("/api/call/turn")
    def turn(req: TurnRequest):
        try:
            return pipeline.turn(req.call_id, req.text).to_dict()
        except KeyError:
            raise HTTPException(status_code=404, detail="알 수 없는 call_id 입니다")

    if WEB.exists():
        app.mount("/static", StaticFiles(directory=WEB), name="static")
    return app


def build_default_app() -> FastAPI:
    from server.config import load_settings
    from server.domain import load_domain
    from server.pipeline import Pipeline
    settings = load_settings()
    domain = load_domain(settings.domains_root / settings.domain)
    return create_app(Pipeline(domain, settings), domain)
