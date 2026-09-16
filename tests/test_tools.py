import pytest
from server.domain import load_domain
from server.tools import make_tools, find_identifiers

TOOL_NAMES = ["search_product", "get_order_status", "get_product_detail", "get_product_options",
              "get_shipping_policy", "get_return_policy", "get_return_status",
              "get_restock_info", "escalate_to_agent"]


@pytest.fixture
def tools(modumall_dir):
    return make_tools(load_domain(modumall_dir))


def test_all_nine_tools_exist(tools):
    assert list(tools) == TOOL_NAMES
    for fn in tools.values():
        assert fn.__doc__ and fn.__doc__.strip(), fn.__name__


def test_unknown_id_returns_error(tools):
    assert "error" in tools["get_product_detail"]("P9999")
    assert "error" in tools["get_order_status"]("O-9999")
    assert "error" in tools["get_return_status"](order_id="O-9999")


def test_teaching_notes_are_stripped(tools):
    o = tools["get_order_status"]("O-1001")
    assert "$teaching_note" not in o
    r = tools["get_return_status"](order_id="O-1006")
    assert not any(k.startswith("$") for k in r)


def test_shipping_shortfall_is_computed_by_tool(tools):
    # 캔버스화(P4001)는 신발, 기준 100,000원
    out = tools["get_shipping_policy"](product_id="P4001", order_amount=59000)
    assert out["free_shipping_threshold"] == 100000
    assert out["shipping_fee"] == 2500
    assert out["shortfall"] == 41000


def test_shipping_no_threshold_category(tools):
    # 어성초 기초 케어 세트(P5002)는 화장품, 무료배송 대상 아님
    out = tools["get_shipping_policy"](product_id="P5002", order_amount=999999)
    assert out["free_shipping_threshold"] is None
    assert out["free_shipping_available"] is False
    assert out["shipping_fee"] == 2500


def test_shipping_without_amount_has_no_shortfall(tools):
    out = tools["get_shipping_policy"](product_id="P4001")
    assert "shortfall" not in out
    assert out["free_shipping_threshold"] == 100000


def test_return_status_keeps_null(tools):
    r = tools["get_return_status"](order_id="O-1006")
    assert r["inspection_result"] is None
    assert r["fault_party"] is None
    assert r["shipping_fee_bearer"] is None


def test_restock_unconfirmed(tools):
    r = tools["get_restock_info"]("P4002")
    assert r["is_confirmed"] is False
    assert r["expected_date"] is None


def test_search_product_single_hit(tools):
    r = tools["search_product"]("캔버스화")
    assert r["resolved_product_id"] == "P4001"
    assert r["ambiguous"] is False


def test_search_product_typo_tolerant(tools):
    r = tools["search_product"]("켄버스화")
    assert r["candidates"] and r["candidates"][0]["product_id"] == "P4001"


def test_search_product_ambiguous(tools):
    r = tools["search_product"]("세트")
    assert r["ambiguous"] is True
    assert r["resolved_product_id"] is None
    assert len(r["candidates"]) >= 2


def test_search_product_no_hit(tools):
    r = tools["search_product"]("청바지")
    assert r["candidates"] == []
    assert r["resolved_product_id"] is None


def test_alias_single_resolves(tools):
    r = tools["search_product"]("원피스")
    assert r["resolved_product_id"] == "P3002" and r["category_query"] is True


def test_alias_multi_is_ambiguous(tools):
    r = tools["search_product"]("신발")
    assert r["ambiguous"] is True and r["category_query"] is True
    assert {c["product_id"] for c in r["candidates"]} == {"P4001", "P4002"}


def test_synonym_maps_to_product(tools):
    r = tools["search_product"]("운동화")
    assert r["resolved_product_id"] == "P4002"


def test_not_in_catalog(tools):
    r = tools["search_product"]("청바지")
    assert r["candidates"] == [] and r["not_in_catalog"] is True


def test_generic_no_hit_is_not_flagged_not_in_catalog(tools):
    r = tools["search_product"]("가방끈")
    assert r["candidates"] == [] and r["not_in_catalog"] is False
    assert r["resolved_product_id"] is None


def test_alias_inside_sentence(tools):
    r = tools["search_product"]("가방 하나 사려는데요")
    assert r["resolved_product_id"] == "P6002"


def test_existing_name_search_unchanged(tools):
    r = tools["search_product"]("캔버스화")
    assert r["resolved_product_id"] == "P4001" and r["category_query"] is False


def test_exact_product_name_beats_alias(tools):
    # "팬티" 는 범주어 사전에 있지만, 문장 전체로 보면 상품명 "브라·팬티 세트" 와 유일하게
    # 가장 높은 점수로 겹친다. 범주어 사전이 정확한 상품명 일치를 가려서는 안 된다(F1).
    r = tools["search_product"]("브라·팬티 세트 80A로 살 건데 팬티는 몇 사이즈로 와요?")
    assert r["resolved_product_id"] == "P1003"
    assert r["ambiguous"] is False


def test_alias_used_when_name_search_ambiguous(tools):
    # "세트" 는 여러 상품명과 동점으로 겹쳐 상품명 검색이 유일한 후보를 내지 못한다.
    # 이때는 "화장품" 이 범주어 사전에 있으므로 범주어 후보로 되돌아간다(F1).
    r = tools["search_product"]("화장품 세트")
    assert r["ambiguous"] is True and r["category_query"] is True
    assert {c["product_id"] for c in r["candidates"]} == {"P5001", "P5002", "P5003"}


def test_find_identifiers():
    assert find_identifiers("O-1006 반품 배송비 누가 내요?") == {"product_id": None, "order_id": "O-1006"}
    assert find_identifiers("P4001 배송비") == {"product_id": "P4001", "order_id": None}
