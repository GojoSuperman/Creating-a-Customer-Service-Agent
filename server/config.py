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


def load_settings() -> Settings:
    return Settings(
        router_model=os.environ.get("ROUTER_MODEL", "gpt-4.1-mini"),
        answer_model=os.environ.get("ANSWER_MODEL", "gpt-4.1"),
        conf_threshold=float(os.environ.get("CONF_THRESHOLD", "0.5")),
        max_tool_turns=int(os.environ.get("MAX_TOOL_TURNS", "3")),
        guardrail_retry=int(os.environ.get("GUARDRAIL_RETRY", "1")),
        domain=os.environ.get("DOMAIN", "modumall"),
        domains_root=ROOT / "domains",
        logs_dir=ROOT / "logs",
        clarify_max=int(os.environ.get("CLARIFY_MAX", "1")),
    )
