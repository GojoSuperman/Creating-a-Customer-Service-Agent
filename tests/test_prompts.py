from pathlib import Path

from server.domain import load_domain
from server.prompts import build_answer_rules


def test_answer_rules_state_priority():
    rules = build_answer_rules(load_domain(Path("domains/modumall")))
    assert "active_process" in rules
    assert "events" in rules and "지나간" in rules
