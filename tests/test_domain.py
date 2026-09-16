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


def test_search_key_is_optional(tmp_path, modumall_dir):
    for f in ["policy.md", "mockdb.json"]:
        (tmp_path / f).write_bytes((modumall_dir / f).read_bytes())
    cfg = json.loads((modumall_dir / "domain.json").read_text(encoding="utf-8"))
    cfg.pop("search", None)
    (tmp_path / "domain.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    d = load_domain(tmp_path)
    assert d.search == {"synonyms": {}, "aliases": {}}


def test_search_alias_ids_must_exist(tmp_path, modumall_dir):
    for f in ["policy.md", "mockdb.json"]:
        (tmp_path / f).write_bytes((modumall_dir / f).read_bytes())
    cfg = json.loads((modumall_dir / "domain.json").read_text(encoding="utf-8"))
    cfg["search"] = {"aliases": {"유령": ["P0000"]}}
    (tmp_path / "domain.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(DomainError) as e:
        load_domain(tmp_path)
    assert "P0000" in str(e.value)


def test_missing_always_section_is_explicit(tmp_path, modumall_dir):
    # always_sections 가 매뉴얼에 없는 장(章)을 가리키면 조용히 빠지는 대신 즉시 터진다.
    (tmp_path / "mockdb.json").write_bytes((modumall_dir / "mockdb.json").read_bytes())
    (tmp_path / "policy.md").write_bytes((modumall_dir / "policy.md").read_bytes())
    cfg = json.loads((modumall_dir / "domain.json").read_text(encoding="utf-8"))
    cfg["always_sections"] = cfg["always_sections"] + ["99"]
    (tmp_path / "domain.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(DomainError) as e:
        load_domain(tmp_path)
    assert "99" in str(e.value)
