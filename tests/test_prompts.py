from server.domain import load_domain
from server.prompts import build_answer_rules


def test_answer_rules_state_priority(modumall_dir):
    rules = build_answer_rules(load_domain(modumall_dir))
    assert "active_process" in rules
    assert "events" in rules and "지나간" in rules
    assert "먼저" in rules and "알리고" in rules
