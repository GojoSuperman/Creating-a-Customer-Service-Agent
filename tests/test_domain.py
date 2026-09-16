import json
import pytest
from server.domain import load_domain, DomainError, ROUTES


def test_load_modumall(modumall_dir):
    d = load_domain(modumall_dir)
    assert d.name == "모두몰"
    assert set(d.routes) == set(ROUTES)
    assert d.routes["SHIPPING"].sections == ["4"]
    assert d.routes["RETURN_REFUND"].sections == ["5", "6"]
    assert d.fixed_values["base_shipping_fee"] == 2500
    assert "## 4. 배송 문의" in d.policy_text
    assert len(d.mockdb["products"]) == 20


def test_missing_file_is_explicit(tmp_path):
    (tmp_path / "domain.json").write_text("{}", encoding="utf-8")
    with pytest.raises(DomainError) as e:
        load_domain(tmp_path)
    assert "policy.md" in str(e.value)


def test_missing_key_is_explicit(tmp_path, modumall_dir):
    for f in ["policy.md", "mockdb.json"]:
        (tmp_path / f).write_bytes((modumall_dir / f).read_bytes())
    cfg = json.loads((modumall_dir / "domain.json").read_text(encoding="utf-8"))
    del cfg["greeting"]
    (tmp_path / "domain.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(DomainError) as e:
        load_domain(tmp_path)
    assert "greeting" in str(e.value)


def test_routes_must_be_exactly_five(tmp_path, modumall_dir):
    for f in ["policy.md", "mockdb.json"]:
        (tmp_path / f).write_bytes((modumall_dir / f).read_bytes())
    cfg = json.loads((modumall_dir / "domain.json").read_text(encoding="utf-8"))
    del cfg["routes"]["OTHER"]
    (tmp_path / "domain.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(DomainError) as e:
        load_domain(tmp_path)
    assert "OTHER" in str(e.value)
