import json
import pytest
from server.domain import load_domain
from server.guardrail import check, log_violation


@pytest.fixture
def domain(modumall_dir):
    return load_domain(modumall_dir)


SHIP = {"get_shipping_policy": {"free_shipping_threshold": 100000, "order_amount": 59000,
                                "shipping_fee": 2500, "shortfall": 41000}}


def test_grounded_answer_passes(domain):
    r = check("무료배송 기준은 100,000원이며 현재 59,000원이라 41,000원이 부족해 배송비 2,500원이 발생합니다.", SHIP, domain)
    assert r.ok, r.violations


def test_hardcoded_number_fails(domain):
    r = check("무료배송 기준은 40,000원 이상입니다.", {}, domain)
    types = {v["type"] for v in r.violations}
    assert "출처 불명 수치" in types
    assert "툴 미호출 단정" in types
    assert not r.ok


def test_derived_arithmetic_is_allowed(domain):
    # 100000 - 59000 = 41000 이 조회 결과에 직접 없어도 한 단계 산술 유도값으로 허용
    res = {"get_shipping_policy": {"free_shipping_threshold": 100000, "order_amount": 59000}}
    r = check("41,000원을 더 담으시면 무료배송입니다.", res, domain)
    assert r.ok


def test_small_numbers_ignored(domain):
    r = check("수도권은 1~2일, 그 외 지역은 3~4일 걸립니다.", {}, domain)
    assert r.ok


def test_fixed_policy_value_allowed_without_tools(domain):
    r = check("반품 배송비는 5,000원입니다.", {}, domain)
    assert r.ok


def test_unconfirmed_restock_assertion_fails(domain):
    res = {"get_restock_info": {"is_confirmed": False, "expected_date": None}}
    r = check("재입고 예정일은 9월 15일입니다.", res, domain)
    assert any(v["type"] == "미확정값 확답" for v in r.violations)


def test_confirmed_restock_assertion_passes(domain):
    res = {"get_restock_info": {"is_confirmed": True, "expected_date": "2026-09-01"}}
    r = check("재입고 예정일은 9월 1일입니다.", res, domain)
    assert r.ok


def test_null_fault_party_assertion_fails(domain):
    res = {"get_return_status": {"inspection_result": None, "fault_party": None,
                                 "shipping_fee_bearer": None}}
    r = check("단순 변심이므로 고객님이 부담하셔야 합니다.", res, domain)
    assert any(v["type"] == "미확정값 확답" for v in r.violations)


def test_null_fault_party_hedged_passes(domain):
    res = {"get_return_status": {"inspection_result": None, "fault_party": None,
                                 "shipping_fee_bearer": None}}
    r = check("검품이 완료되지 않아 배송비 부담 여부는 아직 확정되지 않았습니다.", res, domain)
    assert r.ok


def test_log_violation_appends_jsonl(tmp_path):
    log_violation(tmp_path, {"call_id": "c1", "type": "출처 불명 수치"})
    log_violation(tmp_path, {"call_id": "c2", "type": "미확정값 확답"})
    lines = (tmp_path / "guardrail.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["call_id"] == "c2"
    assert "ts" in json.loads(lines[0])
