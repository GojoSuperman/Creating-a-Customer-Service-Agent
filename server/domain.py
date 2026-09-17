# -*- coding: utf-8 -*-
"""도메인 폴더(domains/<이름>/)를 읽어 Domain 객체로 만든다.

코드는 도메인 이름을 모른다. 매뉴얼·목 DB·라우트 정의·고정값·안내 문구는 전부 여기서 나온다.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

ROUTES = ["ORDER_PLACE", "PRODUCT_INFO", "SHIPPING", "RETURN_REFUND", "OTHER"]
REQUIRED_FILES = ["domain.json", "policy.md", "mockdb.json"]
REQUIRED_KEYS = ["name", "greeting", "out_of_scope_message", "escalate_message", "routes",
                 "always_sections", "routing_rules", "fixed_values", "small_numbers_allowed"]
REQUIRED_DB_KEYS = ["categories", "same_day_delivery", "products", "orders", "returns", "restock"]


class DomainError(ValueError):
    """도메인 폴더가 불완전할 때. 무엇이 빠졌는지 메시지에 적는다."""


@dataclass(frozen=True)
class RouteDef:
    label: str
    definition: str
    sections: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Domain:
    name: str
    greeting: str
    out_of_scope_message: str
    escalate_message: str
    routes: dict[str, RouteDef]
    always_sections: list[str]
    routing_rules: str
    fixed_values: dict
    small_numbers_allowed: list[int]
    policy_text: str
    mockdb: dict
    path: Path
    clarify_message: str
    search: dict
    db_path: Path
    greeting_known: str


def load_domain(path: Path) -> Domain:
    path = Path(path)
    missing = [f for f in REQUIRED_FILES if not (path / f).exists()]
    if missing:
        raise DomainError(f"도메인 폴더 {path} 에 파일이 없습니다: {missing}")

    cfg = json.loads((path / "domain.json").read_text(encoding="utf-8"))
    missing_keys = [k for k in REQUIRED_KEYS if k not in cfg]
    if missing_keys:
        raise DomainError(f"domain.json 에 키가 없습니다: {missing_keys}")

    if set(cfg["routes"]) != set(ROUTES):
        raise DomainError(f"routes 는 정확히 {ROUTES} 여야 합니다. 현재: {sorted(cfg['routes'])}")
    routes = {}
    for name, r in cfg["routes"].items():
        for k in ("label", "definition", "sections"):
            if k not in r:
                raise DomainError(f"routes.{name} 에 '{k}' 가 없습니다")
        routes[name] = RouteDef(r["label"], r["definition"], list(r["sections"]))

    mockdb = json.loads((path / "mockdb.json").read_text(encoding="utf-8"))
    missing_db = [k for k in REQUIRED_DB_KEYS if k not in mockdb]
    if missing_db:
        raise DomainError(f"mockdb.json 에 키가 없습니다: {missing_db}")

    clarify = cfg.get("clarify_message")
    if not clarify:
        labels = ", ".join(routes[k].label for k in ROUTES if k != "OTHER")
        clarify = f"죄송하지만 정확히 확인해 드리기 위해 여쭤봅니다. {labels} 중 어떤 문의이신지 말씀해 주시겠어요?"

    greeting_known = cfg.get("greeting_known") or "{name} 고객님, 안녕하세요. {shop} 고객센터입니다. 무엇을 도와드릴까요?"

    search = cfg.get("search") or {}
    search = {"synonyms": dict(search.get("synonyms") or {}), "aliases": dict(search.get("aliases") or {})}
    product_ids = {p["product_id"] for p in mockdb["products"]}
    for alias, ids in search["aliases"].items():
        unknown = [i for i in ids if i not in product_ids]
        if unknown:
            raise DomainError(f"search.aliases['{alias}'] 에 없는 상품 ID: {unknown}")
    # 동의어 값은 실제 상품명 안에 있어야 한다. 없으면 치환 결과가 늘 빈 후보라 조용히 실패한다.
    # 값이 null 인 항목은 '취급하지 않는 상품' 표시이므로 검증 대상이 아니다.
    flat_names = [p["name"].replace(" ", "") for p in mockdb["products"]]
    for key, value in search["synonyms"].items():
        if value is None:
            continue
        if not any(value.replace(" ", "") in n for n in flat_names):
            raise DomainError(f"search.synonyms['{key}'] 값 '{value}' 이 어떤 상품명에도 없음")

    from server.db.generate import generate
    db_path = generate(path)  # 없으면 만들고, 있으면 그대로

    domain = Domain(
        name=cfg["name"],
        greeting=cfg["greeting"],
        out_of_scope_message=cfg["out_of_scope_message"],
        escalate_message=cfg["escalate_message"],
        routes=routes,
        always_sections=list(cfg["always_sections"]),
        routing_rules=cfg["routing_rules"],
        fixed_values=cfg["fixed_values"],
        small_numbers_allowed=list(cfg["small_numbers_allowed"]),
        policy_text=(path / "policy.md").read_text(encoding="utf-8"),
        mockdb=mockdb,
        path=path,
        clarify_message=clarify,
        search=search,
        db_path=db_path,
        greeting_known=greeting_known,
    )

    # server.context 가 Domain 을 import 하므로 모듈 최상단에서 맞물리면 순환 import가
    # 생긴다. load_domain 안에서 지연 import 해 그 순환을 피한다.
    from server.context import validate_sections
    validate_sections(domain)

    return domain
