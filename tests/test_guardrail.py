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


def test_hedge_in_other_sentence_does_not_mask_assertion(domain):
    # Hedge in sentence 2, but unhedged assertion about expected_date in sentence 1
    # Should trigger "미확정값 확답" violation
    res = {"get_restock_info": {"is_confirmed": False, "expected_date": None}}
    r = check("재입고 예정일은 9월 15일입니다. 검품이 완료되지 않아 배송비 부담 여부는 아직 확정되지 않았습니다.", res, domain)
    assert any(v["type"] == "미확정값 확답" for v in r.violations)


def test_myriad_unit_bypass_is_caught(domain):
    # "10만원" 을 정규화 없이 numbers_in 으로 뽑으면 10 만 잡혀 가드레일을 통과해 버린다.
    r = check("무료배송 기준은 10만원 이상입니다.", {}, domain)
    types = {v["type"] for v in r.violations}
    assert "출처 불명 수치" in types
    assert "툴 미호출 단정" in types
    assert not r.ok


def test_myriad_unit_bypass_with_bu_pattern_is_caught(domain):
    r = check("무료배송 기준은 10만 원부터입니다.", {}, domain)
    types = {v["type"] for v in r.violations}
    assert "출처 불명 수치" in types
    assert "툴 미호출 단정" in types


def test_myriad_and_cheon_combined_converts_correctly(domain):
    res = {"get_shipping_policy": {"free_shipping_threshold": 105000}}
    r = check("무료배송 기준은 10만 5천원입니다.", res, domain)
    assert r.ok, r.violations


def test_cheon_unit_converts_correctly(domain):
    res = {"get_shipping_policy": {"shipping_fee": 2000}}
    r = check("배송비는 2천원입니다.", res, domain)
    assert r.ok, r.violations


def test_grounded_myriad_answer_passes(domain):
    res = {"get_shipping_policy": {"free_shipping_threshold": 100000}}
    r = check("무료배송 기준은 10만원입니다.", res, domain)
    assert r.ok, r.violations


def test_date_components_not_used_for_arithmetic(domain):
    # ISO date components should be allowed directly but not used for arithmetic derivation
    res = {"get_shipping_policy": {"free_shipping_threshold": 100000},
           "get_restock_info": {"expected_date": "2026-09-01", "is_confirmed": True}}

    # 102026 should trigger violation (not 2026 + 100000)
    r = check("102,026원입니다.", res, domain)
    assert any(v["type"] == "출처 불명 수치" for v in r.violations), f"Expected unsourced violation, got {r.violations}"

    # But date components themselves should be allowed
    r2 = check("2026년 9월 1일 입고 예정입니다.", res, domain)
    assert r2.ok, f"Expected ok=True, got violations: {r2.violations}"


def test_normalize_korean_myriad_excludes_unrelated_numbers():
    from server.guardrail import normalize_korean_myriad
    
    assert normalize_korean_myriad("5만 3개") == "50000 3개"


def test_normalize_korean_myriad_with_cheon_won():
    from server.guardrail import normalize_korean_myriad
    
    assert normalize_korean_myriad("10만 5천원") == "105000원"


def test_normalize_korean_myriad_man_only():
    from server.guardrail import normalize_korean_myriad
    
    assert normalize_korean_myriad("10만원") == "100000원"


def test_normalize_korean_myriad_man_only_no_won():
    from server.guardrail import normalize_korean_myriad

    assert normalize_korean_myriad("3만") == "30000"


def test_normalize_korean_myriad_man_cheon_no_unit_merges(domain):
    # F4: "(?=\s*원)" 이었던 예전 lookahead 는 "5만 3천"(뒤에 "원"이 없는 경우)을
    # 50000 3000 으로 쪼갰다. 만 단위는 뒤에 수량/날짜 단위가 없으면 합쳐져야 한다.
    from server.guardrail import normalize_korean_myriad
    assert normalize_korean_myriad("가격은 5만 3천입니다") == "가격은 53000입니다"


def test_normalize_korean_myriad_man_followed_by_count_not_merged(domain):
    from server.guardrail import normalize_korean_myriad
    assert normalize_korean_myriad("5만 3개") == "50000 3개"


def test_normalize_korean_myriad_man_cheon_won_merges(domain):
    from server.guardrail import normalize_korean_myriad
    assert normalize_korean_myriad("10만 5천원") == "105000원"


def test_normalize_korean_myriad_man_cheon_then_count(domain):
    from server.guardrail import normalize_korean_myriad
    assert normalize_korean_myriad("5만 3천 3개") == "53000 3개"


def test_normalize_korean_myriad_man_followed_by_date_not_merged(domain):
    from server.guardrail import normalize_korean_myriad
    assert normalize_korean_myriad("3만 2일 후") == "30000 2일 후"
