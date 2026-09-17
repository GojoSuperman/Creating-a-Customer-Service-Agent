# -*- coding: utf-8 -*-
"""설정 기본값. load_dotenv(ROOT/".env") 가 import 시점에 돌지만 .env 에는
CONF_MARGIN·FOLLOWUP_INHERIT·JUDGE_MODEL 키가 없으므로 delenv 만으로 "미설정"을 만들 수 있다."""
from pathlib import Path

from server.config import Settings, load_settings


def _bare() -> Settings:
    """선택 필드를 하나도 주지 않은 Settings — 데이터클래스 기본값을 그대로 본다."""
    return Settings(router_model="m", answer_model="m", conf_threshold=0.5, max_tool_turns=4,
                    guardrail_retry=1, domain="modumall", domains_root=Path("."),
                    logs_dir=Path("."), clarify_max=1)


def test_conf_margin_dataclass_default_is_0_3():
    assert _bare().conf_margin == 0.3


def test_conf_margin_defaults_to_0_3_when_env_unset(monkeypatch):
    monkeypatch.delenv("CONF_MARGIN", raising=False)
    assert load_settings().conf_margin == 0.3


def test_conf_margin_reads_env(monkeypatch):
    monkeypatch.setenv("CONF_MARGIN", "0.1")
    assert load_settings().conf_margin == 0.1


def test_followup_inherit_defaults_off(monkeypatch):
    monkeypatch.delenv("FOLLOWUP_INHERIT", raising=False)
    assert load_settings().followup_inherit is False


def test_followup_inherit_truthy_values(monkeypatch):
    for v in ("1", "true", " yes "):
        monkeypatch.setenv("FOLLOWUP_INHERIT", v)
        assert load_settings().followup_inherit is True, v


def test_followup_inherit_zero_is_off(monkeypatch):
    monkeypatch.setenv("FOLLOWUP_INHERIT", "0")
    assert load_settings().followup_inherit is False


def test_judge_model_default(monkeypatch):
    monkeypatch.delenv("JUDGE_MODEL", raising=False)
    assert load_settings().judge_model == "gpt-4.1-mini"
