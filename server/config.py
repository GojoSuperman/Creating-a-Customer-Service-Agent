# -*- coding: utf-8 -*-
"""환경 변수에서 설정을 읽는다. 다른 모듈은 여기서만 설정을 가져온다."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    router_model: str
    answer_model: str
    conf_threshold: float
    max_tool_turns: int
    guardrail_retry: int
    domain: str
    domains_root: Path
    logs_dir: Path
    clarify_max: int
    tts_model: str = ""
    tts_voice: str = "nova"
    judge_model: str = "gpt-4.1-mini"
    conf_margin: float = 0.0
    followup_inherit: bool = False   # 후속 발화가 직전 라우트를 강제로 이어받을지. 기본 꺼짐


def load_settings() -> Settings:
    return Settings(
        router_model=os.environ.get("ROUTER_MODEL", "gpt-4.1-mini"),
        answer_model=os.environ.get("ANSWER_MODEL", "gpt-4.1"),
        conf_threshold=float(os.environ.get("CONF_THRESHOLD", "0.5")),
        max_tool_turns=int(os.environ.get("MAX_TOOL_TURNS", "4")),
        guardrail_retry=int(os.environ.get("GUARDRAIL_RETRY", "1")),
        domain=os.environ.get("DOMAIN", "modumall"),
        domains_root=ROOT / "domains",
        logs_dir=ROOT / "logs",
        clarify_max=int(os.environ.get("CLARIFY_MAX", "1")),
        tts_model=os.environ.get("TTS_MODEL", ""),
        tts_voice=os.environ.get("TTS_VOICE", "nova"),
        judge_model=os.environ.get("JUDGE_MODEL", "gpt-4.1-mini"),
        conf_margin=float(os.environ.get("CONF_MARGIN", "0.3")),
        followup_inherit=os.environ.get("FOLLOWUP_INHERIT", "0").strip().lower() in ("1", "true", "yes"),
    )
