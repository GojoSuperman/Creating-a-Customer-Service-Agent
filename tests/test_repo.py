import pytest
from server.repo import Repo, normalize_phone
from server.domain import load_domain


@pytest.fixture(scope="module")
def repo(modumall_dir_module):
    return Repo(load_domain(modumall_dir_module).db_path)


def test_product_dict_shape(repo):
    p = repo.product("P1001")
    assert p["name"] == "요일팬티 7종 세트" and p["is_set"] is True
    assert isinstance(p["components"], list) and p["options"]["size"] == ["S", "M", "L"]
    assert repo.product("P9999") is None
    assert len(repo.products()) >= 170


def test_order_with_items_and_bools(repo):
    o = repo.order("O-1006")
    assert o["return_id"] == "R-2001" and o["is_external_channel"] is False
    assert o["items"][0]["product_id"] and o["items"][0]["qty"] >= 1
    assert repo.order("O-1009")["is_external_channel"] is True
    assert repo.order("O-9999") is None


def test_return_with_history(repo):
    r = repo.return_by_order("O-1006")
    assert r["return_id"] == "R-2001" and r["inspection_result"] is None
    assert [h["stage"] for h in r["stage_history"]][:2] == ["접수", "수거대기"]
    assert repo.return_by_id("R-2001")["order_id"] == "O-1006"


def test_restock_and_refs(repo):
    assert repo.restock("P4002")["is_confirmed"] is False
    assert repo.categories()["SHOES"]["free_shipping_threshold"] == 100000
    assert repo.same_day()["수도권"]["available"] is True


def test_customer_lookup(repo):
    o = repo.order("O-1001")
    c = repo.customer(o["customer_id"])
    assert c["phone"].startswith("010-")
    assert repo.customer_by_phone(c["phone"].replace("-", ""))["customer_id"] == c["customer_id"]
    recent = repo.recent_orders(c["customer_id"], limit=3)
    assert 1 <= len(recent) <= 3 and "items_summary" in recent[0]
    assert repo.customer_by_phone("010-0000-0000") is None


def test_normalize_phone():
    assert normalize_phone("01012345678") == "010-1234-5678"
    assert normalize_phone("010 1234 5678") == "010-1234-5678"
    assert normalize_phone("+82 10-1234-5678") == "010-1234-5678"


def test_call_log_roundtrip(repo):
    repo.log_call("test-call", None, "2026-09-16T10:00:00")
    repo.finish_call("test-call", "2026-09-16T10:03:00", [{"q": "배송비", "action": "ANSWER"}])
    row = repo.call("test-call")
    assert row["ended_at"] == "2026-09-16T10:03:00" and row["turns"][0]["action"] == "ANSWER"


def test_json_cols_size_matching(repo):
    p = repo.product("P1003")
    assert isinstance(p["size_matching"], dict)
    assert "75A" in p["size_matching"] and p["size_matching"]["75A"] == "팬티 90"


def test_json_cols_exchange_target(repo):
    r = repo.return_by_id("R-2003")
    assert isinstance(r["exchange_target"], dict)
    assert r["exchange_target"]["product_id"] == "P4002"


def test_made_to_order_days_mto(repo):
    p = repo.product("P3003")
    assert p["made_to_order"] is True
    assert p["made_to_order_days"] == [5, 7]


def test_made_to_order_days_non_mto(repo):
    p = repo.product("P1001")  # Non-MTO product
    assert p["made_to_order"] is False
    # made_to_order_days should be None or int for non-MTO
    assert p["made_to_order_days"] is None or isinstance(p["made_to_order_days"], int)


def test_concurrent_access(repo):
    import threading

    results = {"exceptions": [], "orders": []}

    def read_order():
        try:
            for _ in range(20):
                order = repo.order("O-1001")
                results["orders"].append(order)
        except Exception as e:
            results["exceptions"].append(e)

    def log_and_finish():
        try:
            repo.log_call("concurrent-test", None, "2026-09-16T10:00:00")
            repo.finish_call("concurrent-test", "2026-09-16T10:03:00", [{"q": "테스트", "action": "ANSWER"}])
        except Exception as e:
            results["exceptions"].append(e)

    threads = [threading.Thread(target=read_order) for _ in range(8)]
    threads.append(threading.Thread(target=log_and_finish))

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results["exceptions"]) == 0, f"Concurrent access errors: {results['exceptions']}"
    assert len(results["orders"]) == 160  # 8 threads × 20 reads
    assert all(o["customer_id"] == "C-0001" for o in results["orders"])
