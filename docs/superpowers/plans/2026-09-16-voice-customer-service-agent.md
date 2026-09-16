# 음성 고객 응대 에이전트 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 브라우저에서 전화처럼 음성으로 대화하는 고객 응대 에이전트. 문의를 분류하고, 목 DB를 조회하고, 매뉴얼에 근거해 답하며, 가드레일이 출처 없는 숫자를 막는다.

**Architecture:** FastAPI 서버가 LangGraph 파이프라인(route → answer → guard → escalate)을 감싸고 정적 화면을 서빙한다. 도메인 데이터는 `domains/<이름>/` 폴더에서 로드하며 코드는 도메인 이름을 모른다. 화면은 Web Speech API로 음성 입출력을 처리하고 턴마다 HTTP POST 한 번을 보낸다.

**Tech Stack:** Python 3.12 (uv), FastAPI, uvicorn, LangGraph, langchain, langchain-openai, pydantic, pandas, scikit-learn, pytest, httpx. 화면은 의존성 없는 HTML/JS.

**Spec:** `docs/superpowers/specs/2026-09-16-voice-customer-service-agent-design.md`

## Global Constraints

- Python 3.12 가상환경 (`.venv`, uv로 생성). 시스템 Python 3.14는 쓰지 않는다.
- 라우트 이름 5개 고정: `ORDER_PLACE`, `PRODUCT_INFO`, `SHIPPING`, `RETURN_REFUND`, `OTHER`.
- 도구 9개 이름 고정: `search_product`, `get_order_status`, `get_product_detail`, `get_product_options`, `get_shipping_policy`, `get_return_policy`, `get_return_status`, `get_restock_info`, `escalate_to_agent`.
- 선택 인자는 반드시 `Optional[...]`로 적는다.
- 모든 `pytest`는 API 키 없이 통과해야 한다. LLM은 주입 가능한 함수/러너블로 받는다.
- 평가 스크립트는 `split == "eval"`만 쓰고 `fewshot`은 프롬프트에 넣지 않는다.
- 코드 식별자는 영어, 주석·로그·UI 문구는 한국어.
- 커밋 메시지 끝에 `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- 시크릿(`.env`)은 커밋하지 않는다.

## 파일 구조

| 파일 | 책임 |
|---|---|
| `server/config.py` | 환경 변수 → 설정값. 다른 모듈은 여기서만 설정을 읽는다 |
| `server/domain.py` | `domains/<이름>/` 로드·검증. `Domain` 데이터클래스 |
| `server/context.py` | 매뉴얼 분할, 라우트별 컨텍스트 |
| `server/tools.py` | 목 DB 조회 도구 9개. `make_tools(domain)`가 함수 dict를 만든다 |
| `server/guardrail.py` | 숫자 출처 검사, 확답 패턴 검사, 위반 로그 |
| `server/prompts.py` | 프롬프트 뼈대 문자열과 조립 함수 |
| `server/router.py` | 분류 그래프 (classify 주입 가능) |
| `server/answer.py` | 도구 호출 루프 그래프 (LLM 주입 가능) |
| `server/pipeline.py` | 전체 그래프, 체크포인터, `Pipeline` 클래스 |
| `server/app.py` | FastAPI 앱 팩토리 `create_app(pipeline, domain)` |
| `web/index.html`, `web/call.js`, `web/voice.js`, `web/panel.js` | 화면 |
| `domains/modumall/*` | 모두몰 데이터 |
| `eval/eval_router.py`, `eval/eval_answer.py`, `eval/scoring.py` | 평가 |
| `tests/*` | 단위 테스트 |

---

### Task 1: 프로젝트 골격과 도메인 데이터

**Files:**
- Create: `pyproject.toml`, `requirements.txt`, `.env.example`, `.gitignore`(수정), `server/__init__.py`, `eval/__init__.py`, `tests/__init__.py`, `tests/conftest.py`
- Create: `domains/modumall/policy.md`, `domains/modumall/mockdb.json`, `domains/modumall/eval/customer_inquiries.csv`, `domains/modumall/eval/routing_answers.csv`, `domains/modumall/eval/hard_cases.csv`, `domains/modumall/eval/answer_goldenset.json`

**Interfaces:**
- Produces: `tests/conftest.py`의 `ROOT` 상수(프로젝트 루트 `Path`)와 `modumall_dir` fixture(`domains/modumall` 경로)

- [ ] **Step 1: 가상환경과 의존성**

```bash
cd /home/nanumcom/projects/Creating-a-Customer-Service-Agent
uv venv --python 3.12 .venv
```

`requirements.txt`:

```
fastapi>=0.115
uvicorn[standard]>=0.30
langchain>=1.0
langchain-openai>=1.0
langgraph>=1.0
pydantic>=2.7
pandas>=2.2
scikit-learn>=1.5
python-dotenv>=1.0
pytest>=8
httpx>=0.27
```

```bash
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -c "import langchain, langgraph, fastapi; print('ok')"
```

Expected: `ok`

- [ ] **Step 2: pyproject.toml (pytest 경로 설정)**

```toml
[project]
name = "voice-customer-service-agent"
version = "0.1.0"
requires-python = ">=3.12"

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

- [ ] **Step 3: .env.example과 .gitignore**

`.env.example`:

```
OPENAI_API_KEY=sk-...
# 선택
# ROUTER_MODEL=gpt-4.1-mini
# ANSWER_MODEL=gpt-4.1
# DOMAIN=modumall
# CONF_THRESHOLD=0.5
```

`.gitignore`에 추가:

```
.venv/
__pycache__/
*.pyc
.env
.env.local
logs/
.pytest_cache/
```

- [ ] **Step 4: 도메인 데이터 복사**

```bash
SRC="/mnt/c/Users/minah/Downloads/수업과제/4.고객 응대 에이전트 만들기 - 라우팅과 그라운딩/modumall-agent-data/modumall-agent-data"
mkdir -p domains/modumall/eval
cp "$SRC/policy_modumall.md" domains/modumall/policy.md
cp "$SRC/mockdata_modumall.json" domains/modumall/mockdb.json
cp "$SRC/customer_inquiries.csv" "$SRC/routing_answers.csv" "$SRC/hard_cases.csv" domains/modumall/eval/
cp "$SRC/answer_goldenset_multiturn.json" domains/modumall/eval/answer_goldenset.json
ls -la domains/modumall domains/modumall/eval
```

Expected: 파일 6개. CSV는 BOM(`﻿`)이 있으므로 읽을 때 `encoding="utf-8-sig"`를 쓴다.

- [ ] **Step 5: 패키지 파일과 conftest**

`server/__init__.py`, `eval/__init__.py`, `tests/__init__.py`는 빈 파일.

`tests/conftest.py`:

```python
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def modumall_dir() -> Path:
    return ROOT / "domains" / "modumall"
```

- [ ] **Step 6: 테스트 러너 확인**

Run: `.venv/bin/pytest -q`
Expected: `no tests ran` (오류 없음)

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml requirements.txt .env.example .gitignore server eval tests domains
git commit -m "chore: 프로젝트 골격과 모두몰 도메인 데이터

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: config.py와 domain.py

**Files:**
- Create: `server/config.py`, `server/domain.py`, `domains/modumall/domain.json`
- Test: `tests/test_domain.py`

**Interfaces:**
- Produces:
  - `config.Settings` 데이터클래스: `router_model: str`, `answer_model: str`, `conf_threshold: float`, `max_tool_turns: int`, `guardrail_retry: int`, `domain: str`, `domains_root: Path`, `logs_dir: Path`. `config.load_settings() -> Settings`.
  - `domain.Domain` 데이터클래스: `name: str`, `greeting: str`, `out_of_scope_message: str`, `escalate_message: str`, `routes: dict[str, RouteDef]`, `always_sections: list[str]`, `routing_rules: str`, `fixed_values: dict[str, int | list[int] | str]`, `small_numbers_allowed: list[int]`, `policy_text: str`, `mockdb: dict`, `path: Path`.
  - `domain.RouteDef` 데이터클래스: `label: str`, `definition: str`, `sections: list[str]`.
  - `domain.load_domain(path: Path) -> Domain`. 누락 시 `DomainError(ValueError)`.
  - `domain.ROUTES = ["ORDER_PLACE", "PRODUCT_INFO", "SHIPPING", "RETURN_REFUND", "OTHER"]`

- [ ] **Step 1: 실패하는 테스트**

`tests/test_domain.py`:

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_domain.py -v`
Expected: FAIL, `ModuleNotFoundError: server.domain`

- [ ] **Step 3: domain.json 작성**

`domains/modumall/domain.json`:

```json
{
  "name": "모두몰",
  "greeting": "안녕하세요, 모두몰 고객센터입니다. 무엇을 도와드릴까요?",
  "out_of_scope_message": "해당 내용은 저희 모두몰에서 안내드리기 어려운 부분입니다. 주문하신 사이트나 해당 업체의 고객센터로 문의해 주시기 바랍니다. 감사합니다.",
  "escalate_message": "정확한 확인을 위해 상담원에게 연결해 드리겠습니다. 잠시만 기다려 주세요.",
  "routes": {
    "ORDER_PLACE": {
      "label": "주문·구매",
      "definition": "구매·주문 접수, 구매 가능 여부, 수량·옵션 변경 요청 (담당: 주문)",
      "sections": ["2"]
    },
    "PRODUCT_INFO": {
      "label": "상품 문의",
      "definition": "상품 구성·품질·소재·사이즈·재고 문의 (담당: 상품)",
      "sections": ["3"]
    },
    "SHIPPING": {
      "label": "배송",
      "definition": "배송비, 배송 기간, 배송 조회, 무료배송 조건 (담당: 배송)",
      "sections": ["4"]
    },
    "RETURN_REFUND": {
      "label": "교환·반품·환불",
      "definition": "교환·반품·환불 신청 및 처리 현황 (담당: 반품)",
      "sections": ["5", "6"]
    },
    "OTHER": {
      "label": "범위 밖",
      "definition": "위 네 유형에 해당하지 않는 응대 범위 밖 문의",
      "sections": []
    }
  },
  "always_sections": ["이 매뉴얼을 쓰는 방법", "0", "1", "7", "8"],
  "routing_rules": "[분류 원칙 — 매뉴얼 1.1]\n고객이 쓴 단어가 아니라, 고객이 원하는 결과로 판단한다.\nORDER_PLACE 와 PRODUCT_INFO 는 어휘가 거의 같다(\"살 수 있나요\"). 바로 구매를 진행할 의사가 보이면 ORDER_PLACE, 살지 말지 판단하려고 정보를 묻는 것이면 PRODUCT_INFO 다.\n\n[분류 우선순위 — 매뉴얼 1.2]\n1) 응대 범위 밖이면 OTHER\n2) 진행 중인 반품·교환 건에 관한 문의면 RETURN_REFUND\n3) 그 외는 위 네 라우트\n\n[범위 밖 판단 — 매뉴얼 7.1]\n모두몰은 온라인 전용이며 오프라인 매장이 없다. 매장 위치·영업시간·주차 문의, 타 오픈마켓·제휴몰 주문 건, 제조사 A/S 문의는 모두 OTHER 다.",
  "fixed_values": {
    "base_shipping_fee": 2500,
    "return_fee_full": 5000,
    "return_fee_partial": 2500,
    "cutoff_hour": 11,
    "delivery_days_metro": [1, 2],
    "delivery_days_other": [3, 4],
    "delivery_days_std": [1, 3],
    "return_total_days": [3, 7],
    "inspect_days": [2, 3],
    "pickup_days": [1, 2],
    "designated_courier": "롯데택배"
  },
  "small_numbers_allowed": [1, 2, 3, 4, 5, 7, 11]
}
```

- [ ] **Step 4: config.py**

```python
# -*- coding: utf-8 -*-
"""환경 변수에서 설정을 읽는다. 다른 모듈은 여기서만 설정을 가져온다."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    router_model: str
    answer_model: str
    conf_threshold: float
    max_tool_turns: int
    guardrail_retry: int
    domain: str
    domains_root: Path
    logs_dir: Path


def load_settings() -> Settings:
    return Settings(
        router_model=os.environ.get("ROUTER_MODEL", "gpt-4.1-mini"),
        answer_model=os.environ.get("ANSWER_MODEL", "gpt-4.1"),
        conf_threshold=float(os.environ.get("CONF_THRESHOLD", "0.5")),
        max_tool_turns=int(os.environ.get("MAX_TOOL_TURNS", "3")),
        guardrail_retry=int(os.environ.get("GUARDRAIL_RETRY", "1")),
        domain=os.environ.get("DOMAIN", "modumall"),
        domains_root=ROOT / "domains",
        logs_dir=ROOT / "logs",
    )
```

- [ ] **Step 5: domain.py**

```python
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

    return Domain(
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
    )
```

- [ ] **Step 6: 통과 확인**

Run: `.venv/bin/pytest tests/test_domain.py -v`
Expected: 4 passed

- [ ] **Step 7: Commit**

```bash
git add server/config.py server/domain.py domains/modumall/domain.json tests/test_domain.py
git commit -m "feat: 설정 로더와 도메인 폴더 로더

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: context.py — 매뉴얼 분할과 라우트별 컨텍스트

**Files:**
- Create: `server/context.py`
- Test: `tests/test_context.py`

**Interfaces:**
- Consumes: `Domain` (Task 2)
- Produces:
  - `split_sections(text: str) -> dict[str, str]` — 키는 장 번호 문자열(`"4"`) 또는 번호 없는 제목, `"_header"`는 첫 `##` 이전 본문. `부록`으로 시작하는 장 제외.
  - `build_context(domain: Domain, route: str) -> str`

- [ ] **Step 1: 실패하는 테스트**

`tests/test_context.py`:

```python
from server.domain import load_domain
from server.context import split_sections, build_context

SAMPLE = """# 제목
머리말입니다.

## 이 매뉴얼을 쓰는 방법
규약.

## 0. 원칙
원칙 본문.

## 4. 배송 문의
### 4.1 배송비
기본 배송비 2,500원.

## 6. 반품 비용
반품 배송비 5,000원.

## 부록 A. 요약
부록 본문.
"""


def test_split_sections_keys_and_appendix_excluded():
    s = split_sections(SAMPLE)
    assert s["_header"] == "# 제목\n머리말입니다."
    assert set(s) == {"_header", "이 매뉴얼을 쓰는 방법", "0", "4", "6"}
    assert s["4"].startswith("## 4. 배송 문의")
    assert "4.1 배송비" in s["4"]


def test_shipping_context_excludes_return_sections(modumall_dir):
    d = load_domain(modumall_dir)
    ctx = build_context(d, "SHIPPING")
    assert "## 4. 배송 문의" in ctx
    assert "## 0. 상담 기본 원칙" in ctx
    assert "## 7. 응대 범위와 이관" in ctx
    assert "## 5. 교환·반품·환불" not in ctx
    assert "## 6. 교환·반품·환불" not in ctx
    assert "## 부록" not in ctx


def test_return_context_has_both_chapters(modumall_dir):
    d = load_domain(modumall_dir)
    ctx = build_context(d, "RETURN_REFUND")
    assert "## 5. 교환·반품·환불" in ctx
    assert "## 6. 교환·반품·환불" in ctx
    assert "## 4. 배송 문의" not in ctx


def test_other_context_is_only_always_sections(modumall_dir):
    d = load_domain(modumall_dir)
    ctx = build_context(d, "OTHER")
    assert "## 7. 응대 범위와 이관" in ctx
    assert "## 2. 주문" not in ctx
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_context.py -v`
Expected: FAIL, `ModuleNotFoundError: server.context`

- [ ] **Step 3: 구현**

`server/context.py`:

```python
# -*- coding: utf-8 -*-
"""매뉴얼을 장 단위로 쪼개고, 라우트에 필요한 장만 골라 컨텍스트를 만든다.

전문을 넣지 않는 이유: 입력이 길어 비용·지연이 늘고, 관련 없는 정책이 오답을 유도한다
(배송 문의에 반품 배송비가 섞여 들어가는 식).
"""
import re
from functools import lru_cache

from server.domain import Domain


def split_sections(text: str) -> dict[str, str]:
    parts = re.split(r"^## ", text, flags=re.M)
    out = {"_header": parts[0].strip()}
    for p in parts[1:]:
        title = p.split("\n", 1)[0].strip()
        if title.startswith("부록"):
            continue
        m = re.match(r"(\d+)\.", title)
        key = m.group(1) if m else title
        out[key] = "## " + p.rstrip()
    return out


@lru_cache(maxsize=8)
def _sections_for(policy_text: str) -> dict[str, str]:
    return split_sections(policy_text)


def build_context(domain: Domain, route: str) -> str:
    secs = _sections_for(domain.policy_text)
    keys = domain.always_sections + domain.routes[route].sections
    chosen = [secs[k] for k in keys if k in secs]
    return "\n\n".join([secs["_header"]] + chosen)
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_context.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add server/context.py tests/test_context.py
git commit -m "feat: 매뉴얼 분할과 라우트별 컨텍스트 조립

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: tools.py — 조회 도구 9개

**Files:**
- Create: `server/tools.py`
- Test: `tests/test_tools.py`

**Interfaces:**
- Consumes: `Domain.mockdb`, `Domain.fixed_values`
- Produces:
  - `make_tools(domain: Domain) -> dict[str, Callable]` — 키는 도구 이름 9개, 값은 타입 힌트·docstring이 붙은 일반 함수(LangChain `@tool`은 answer.py에서 감싼다).
  - `find_identifiers(text: str) -> dict` — `{"product_id": "P4001" | None, "order_id": "O-1006" | None}` (평가·화면 로그용)
  - 모든 도구 반환은 `dict`. 없는 ID는 `{"error": "...", ...}`. `$`로 시작하는 키는 제거.

- [ ] **Step 1: 실패하는 테스트**

`tests/test_tools.py`:

```python
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


def test_find_identifiers():
    assert find_identifiers("O-1006 반품 배송비 누가 내요?") == {"product_id": None, "order_id": "O-1006"}
    assert find_identifiers("P4001 배송비") == {"product_id": "P4001", "order_id": None}
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_tools.py -v`
Expected: FAIL, `ModuleNotFoundError: server.tools`

- [ ] **Step 3: 구현**

`server/tools.py`:

```python
# -*- coding: utf-8 -*-
"""어드민 조회 도구. 매뉴얼의 [어드민 조회] 표시에서 도출한 것들이다.

주의 두 가지.
- 선택 인자는 반드시 Optional[...] 로 적는다. int 인데 기본값이 None 이면 모델이 None 을
  보냈을 때 스키마 검증에 걸려 왕복이 낭비된다.
- docstring 첫 줄이 모델이 읽는 도구 설명이다. 여기를 고치면 도구 선택이 달라진다.
"""
import difflib
import re
from typing import Callable, Optional

from server.domain import Domain

CAPITAL = ("서울", "경기", "인천", "수도권")


def clean(obj):
    """$ 로 시작하는 내부 주석 키를 재귀적으로 제거한다."""
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items() if not k.startswith("$")}
    if isinstance(obj, list):
        return [clean(v) for v in obj]
    return obj


def find_identifiers(text: str) -> dict:
    """발화에서 상품 ID(P0000)와 주문번호(O-0000)를 찾는다."""
    p = re.search(r"P\d{4}", text)
    o = re.search(r"O-\d{4}", text)
    return {"product_id": p.group(0) if p else None, "order_id": o.group(0) if o else None}


def _normalize_region(region: Optional[str]) -> Optional[str]:
    if not region:
        return None
    if any(k in region for k in ("제주", "도서", "산간", "울릉")):
        return "제주도서산간"
    if any(k in region for k in CAPITAL):
        return "수도권"
    return "수도권외"


def _toks(s: str) -> list[str]:
    return [t for t in re.split(r"[\s·()]+", s) if t]


def make_tools(domain: Domain) -> dict[str, Callable]:
    db = domain.mockdb
    products = {p["product_id"]: p for p in db["products"]}
    orders = {o["order_id"]: o for o in db["orders"]}
    returns = {r["return_id"]: r for r in db["returns"]}
    returns_by_order = {r["order_id"]: r for r in db["returns"]}
    restock = {r["product_id"]: r for r in db["restock"]}
    categories = db["categories"]
    same_day = db["same_day_delivery"]
    base_fee = domain.fixed_values["base_shipping_fee"]

    def search_product(query: str) -> dict:
        """상품명 일부로 상품을 찾는다. 상품 ID를 모를 때 가장 먼저 부르는 도구다.

        후보를 점수와 함께 돌려준다. 후보가 여럿이면(ambiguous) 확정하지 말고 고객에게 되물어야 한다.
        """
        qt = _toks(query)
        if not qt:
            return {"query": query, "candidates": [], "resolved_product_id": None, "ambiguous": False}
        flat_q = query.replace(" ", "")
        hits = []
        for pid, p in products.items():
            name = p["name"]
            flat = name.replace(" ", "")
            nt = _toks(name)
            overlap = sum(1 for t in qt if t in flat or any(t in x or x in t for x in nt))
            score = overlap / len(qt)
            if score == 0:
                # 오타 보정: 공백 제거 문자열 유사도
                ratio = difflib.SequenceMatcher(None, flat_q, flat).ratio()
                if ratio >= 0.6:
                    score = round(ratio * 0.9, 2)
            if score > 0:
                hits.append({"product_id": pid, "name": name, "category": p["category"],
                             "price": p["price"], "score": round(score, 2)})
        hits.sort(key=lambda h: -h["score"])
        top = [h for h in hits if h["score"] == hits[0]["score"]] if hits else []
        ambiguous = len(top) > 1
        return {"query": query, "candidates": hits[:5],
                "resolved_product_id": top[0]["product_id"] if len(top) == 1 else None,
                "ambiguous": ambiguous,
                "note": "후보가 여러 개입니다. 어느 상품인지 고객에게 확인하십시오." if ambiguous else None}

    def get_order_status(order_id: str) -> dict:
        """주문번호로 주문의 현재 진행 단계와 배송 정보를 조회한다."""
        o = orders.get(order_id)
        if not o:
            return {"error": "주문을 찾을 수 없습니다", "order_id": order_id}
        keys = ["order_id", "status", "status_detail", "is_external_channel", "items",
                "order_amount", "shipping_fee", "address_region", "courier", "tracking_no",
                "invoice_printed", "expected_ship_date"]
        return clean({k: o[k] for k in keys if k in o})

    def get_product_detail(product_id: str) -> dict:
        """상품 ID로 구성·소재·원산지·재고·보증서 동봉 여부를 조회한다."""
        p = products.get(product_id)
        if not p:
            return {"error": "상품을 찾을 수 없습니다", "product_id": product_id}
        keys = ["product_id", "name", "category", "price", "stock", "components", "material",
                "origin", "has_quality_cert", "made_to_order", "size_chart"]
        out = {k: p[k] for k in keys if k in p}
        out["category_label"] = categories[p["category"]]["label"]
        return clean(out)

    def get_product_options(product_id: str) -> dict:
        """상품 ID로 개별(단품) 구매 가능 여부와 판매 옵션을 조회한다."""
        p = products.get(product_id)
        if not p:
            return {"error": "상품을 찾을 수 없습니다", "product_id": product_id}
        keys = ["product_id", "name", "is_set", "components", "options",
                "individual_purchase_allowed", "individual_purchase_note", "made_to_order"]
        return clean({k: p[k] for k in keys if k in p})

    def _region_info(region: str) -> dict:
        key = _normalize_region(region)
        sd = clean(same_day.get(key, {}))
        return {"region_input": region, "region": key,
                "same_day_available": sd.get("available", False),
                "same_day_fee": sd.get("fee"),
                "extra_shipping_fee": sd.get("extra_fee"),
                "region_note": sd.get("note")}

    def get_shipping_policy(product_id: Optional[str] = None, category: Optional[str] = None,
                            order_amount: Optional[int] = None,
                            region: Optional[str] = None) -> dict:
        """무료배송 기준액과 배송비를 조회한다. 금액을 주면 부족액까지 계산한다.

        상품 ID 로도 카테고리로도 조회할 수 있다. region 을 주면 당일배송·도서산간 정보도 함께 준다.
        """
        p = products.get(product_id) if product_id else None
        if product_id and not p:
            return {"error": "상품을 찾을 수 없습니다", "product_id": product_id}
        cat_key = p["category"] if p else category
        if cat_key is None:
            out = {"base_shipping_fee": base_fee,
                   "note": "무료배송 기준은 카테고리마다 다릅니다. 상품 또는 카테고리를 지정해 주세요."}
            if region:
                out.update(_region_info(region))
            return clean(out)
        if cat_key not in categories:
            return {"error": "카테고리를 찾을 수 없습니다", "category": cat_key}
        cat = categories[cat_key]
        th = cat["free_shipping_threshold"]
        out = {"product_id": product_id, "name": p["name"] if p else None, "category": cat_key,
               "category_label": cat["label"], "price": p["price"] if p else None,
               "base_shipping_fee": base_fee, "free_shipping_threshold": th,
               "free_shipping_available": th is not None}
        if th is None:
            out["note"] = cat.get("free_shipping_note", "무료배송 대상이 아닙니다.")
        if order_amount is not None:
            out["order_amount"] = order_amount
            if th is not None and order_amount >= th:
                out["free_shipping_applied"] = True
                out["shipping_fee"] = 0
            else:
                out["free_shipping_applied"] = False
                out["shipping_fee"] = base_fee
                if th is not None:
                    out["shortfall"] = th - order_amount   # 부족액은 코드가 계산한다
        if region:
            out.update(_region_info(region))
        return clean(out)

    def get_return_policy(product_id: Optional[str] = None, order_id: Optional[str] = None) -> dict:
        """상품 또는 주문의 반품 가능 기간과 조건을 조회한다."""
        if order_id:
            o = orders.get(order_id)
            if not o:
                return {"error": "주문을 찾을 수 없습니다", "order_id": order_id}
            product_id = o["items"][0]["product_id"]
        p = products.get(product_id)
        if not p:
            return {"error": "상품을 찾을 수 없습니다", "product_id": product_id}
        cat = categories[p["category"]]
        mto = bool(p.get("made_to_order"))
        return clean({
            "product_id": product_id, "name": p["name"], "category": p["category"],
            "category_label": cat["label"],
            "return_window_days": cat["return_window_days"],
            "return_window_basis": cat["return_window_basis"],
            "requires_unopened": cat["requires_unopened"],
            "made_to_order": mto,
            "return_blocked": mto,
            "return_blocked_reason": "주문 제작 상품은 교환·반품이 불가합니다." if mto else None,
        })

    def get_return_status(order_id: Optional[str] = None, return_id: Optional[str] = None) -> dict:
        """반품·교환의 현재 처리 단계를 조회한다. 검품 전에는 귀책이 확정되지 않는다(null)."""
        r = returns.get(return_id) if return_id else returns_by_order.get(order_id)
        if not r:
            return {"error": "반품 접수 내역을 찾을 수 없습니다",
                    "order_id": order_id, "return_id": return_id}
        return clean(r)

    def get_restock_info(product_id: str) -> dict:
        """품절 상품의 재입고 확정 여부와 예정일을 조회한다. is_confirmed 가 false 면 예정일을 확답하지 않는다."""
        r = restock.get(product_id)
        if not r:
            p = products.get(product_id)
            if p and p.get("stock"):
                return {"product_id": product_id, "is_soldout": False, "stock": p["stock"],
                        "note": "재고가 있어 재입고 대기 상품이 아닙니다."}
            return {"error": "재입고 정보를 찾을 수 없습니다", "product_id": product_id}
        return clean(r)

    def escalate_to_agent(reason: str, context: Optional[dict] = None) -> dict:
        """상담원에게 이관한다. 매뉴얼 7.2의 이관 기준에 해당할 때 호출한다."""
        return {"escalated": True, "reason": reason, "context": context or {},
                "message": domain.escalate_message}

    return {f.__name__: f for f in [search_product, get_order_status, get_product_detail,
                                    get_product_options, get_shipping_policy, get_return_policy,
                                    get_return_status, get_restock_info, escalate_to_agent]}
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_tools.py -v`
Expected: 13 passed. `test_search_product_typo_tolerant`가 실패하면 유사도 임계값 0.6을 0.5로 낮추고 다시 확인한다. `test_search_product_ambiguous`가 실패하면 "세트"가 여러 상품명에 들어 있는지 확인한다(요일팬티 7종 세트, 오가닉 팬티 5매 세트, 브라·팬티 세트 등 최소 5개).

- [ ] **Step 5: Commit**

```bash
git add server/tools.py tests/test_tools.py
git commit -m "feat: 목 DB 조회 도구 9개와 오타 보정 상품 검색

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: guardrail.py — 숫자 출처 역추적과 확답 검사

**Files:**
- Create: `server/guardrail.py`
- Test: `tests/test_guardrail.py`

**Interfaces:**
- Consumes: `Domain.fixed_values`, `Domain.small_numbers_allowed`
- Produces:
  - `check(answer: str, tool_results: dict, domain: Domain, min_check: int = 1000) -> GuardResult`
  - `GuardResult` 데이터클래스: `ok: bool`, `violations: list[dict]` (각 `{"type": str, "detail": str}`), `numbers_in_answer: list[int]`, `from_tools: list[int]`. `to_dict()` 제공.
  - `log_violation(logs_dir: Path, record: dict) -> None` — `logs/guardrail.jsonl`에 한 줄 추가.
  - 위반 유형 문자열: `"출처 불명 수치"`, `"툴 미호출 단정"`, `"미확정값 확답"`

- [ ] **Step 1: 실패하는 테스트**

`tests/test_guardrail.py`:

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_guardrail.py -v`
Expected: FAIL, `ModuleNotFoundError: server.guardrail`

- [ ] **Step 3: 구현**

`server/guardrail.py`:

```python
# -*- coding: utf-8 -*-
"""답변 속 숫자의 출처를 역추적한다.

허용 집합 = 조회 결과 + 도메인 고정값 + 한 단계 산술 유도값.
출처를 못 찾는 숫자가 있으면 위반. 여기에 미확정값(null / is_confirmed=false)을
확답하는 패턴 검사를 더한다. 숫자·패턴이 아닌 오류는 못 잡는다 — 그건 정답셋 채점기 몫이다.
"""
import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

from server.domain import Domain

VIOLATION_UNSOURCED = "출처 불명 수치"
VIOLATION_NO_TOOL = "툴 미호출 단정"
VIOLATION_ASSERT_UNCONFIRMED = "미확정값 확답"

# 미확정 필드별로, 답변에서 "확답"으로 간주할 패턴
ASSERTION_PATTERNS = {
    "expected_date": [r"예정일은\s*\S+\s*(입니다|이에요|예요)", r"\d+월\s*\d+일에?\s*(입고|재입고)"],
    "fault_party": [r"(고객|구매자)\s*(님)?\s*(이|께서)?\s*부담하(셔야|시게|십니다)",
                    r"(단순\s*변심|하자)(이므로|으로)\s*.*부담"],
    "shipping_fee_bearer": [r"(고객|구매자)\s*(님)?\s*(이|께서)?\s*부담하(셔야|시게|십니다)",
                            r"배송비는?\s*(고객|저희|판매자)\s*(님)?\s*(이|가)?\s*부담"],
    "inspection_result": [r"검품\s*결과\s*(하자|정상)(로|으로)\s*(확인|판정)"],
}
UNCONFIRMED_HEDGE = r"(확정되지\s*않|미확정|아직\s*확인|검품\s*(후|이\s*완료되)|정해지지\s*않)"


@dataclass
class GuardResult:
    ok: bool
    violations: list = field(default_factory=list)
    numbers_in_answer: list = field(default_factory=list)
    from_tools: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def numbers_in(obj) -> set[int]:
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    text = re.sub(r"(?<=\d),(?=\d)", "", text)
    return {int(m) for m in re.findall(r"\d+", text)}


def allowed_numbers(tool_results: dict, domain: Domain) -> tuple[set[int], set[int]]:
    allowed: set[int] = set(domain.small_numbers_allowed)
    for v in domain.fixed_values.values():
        if isinstance(v, int):
            allowed.add(v)
        elif isinstance(v, list):
            allowed.update(x for x in v if isinstance(x, int))
    tool_nums: set[int] = set()
    for r in (tool_results or {}).values():
        tool_nums |= numbers_in(r)
    allowed |= tool_nums
    base = sorted(allowed)
    for a in base:
        for b in base:
            if a > b:
                allowed.add(a - b)
            allowed.add(a + b)
    return allowed, tool_nums


def _unconfirmed_fields(tool_results: dict) -> set[str]:
    """조회 결과에서 '아직 정해지지 않음'을 뜻하는 필드 이름을 모은다."""
    out = set()
    for r in (tool_results or {}).values():
        if not isinstance(r, dict):
            continue
        if r.get("is_confirmed") is False:
            out.add("expected_date")
        for k in ("fault_party", "shipping_fee_bearer", "inspection_result"):
            if k in r and r[k] is None:
                out.add(k)
    return out


def check(answer: str, tool_results: dict, domain: Domain, min_check: int = 1000) -> GuardResult:
    allowed, tool_nums = allowed_numbers(tool_results, domain)
    found = numbers_in(answer)
    suspicious = sorted(n for n in found if n >= min_check and n not in allowed)
    violations = []
    if suspicious:
        violations.append({"type": VIOLATION_UNSOURCED,
                           "detail": f"조회 결과·매뉴얼 고정값에 없는 숫자: {suspicious}"})
    if re.search(r"무료\s?배송", answer) and re.search(r"\d[\d,]*\s*원\s*(이상|부터)", answer):
        if "get_shipping_policy" not in (tool_results or {}):
            violations.append({"type": VIOLATION_NO_TOOL,
                               "detail": "무료배송 기준액을 get_shipping_policy 조회 없이 단정"})
    hedged = re.search(UNCONFIRMED_HEDGE, answer) is not None
    for fld in _unconfirmed_fields(tool_results):
        for pat in ASSERTION_PATTERNS.get(fld, []):
            if re.search(pat, answer) and not hedged:
                violations.append({"type": VIOLATION_ASSERT_UNCONFIRMED,
                                   "detail": f"{fld} 가 미확정인데 확답 패턴 발견: /{pat}/"})
                break
    return GuardResult(ok=not violations, violations=violations,
                       numbers_in_answer=sorted(found), from_tools=sorted(tool_nums))


def log_violation(logs_dir: Path, record: dict) -> None:
    logs_dir = Path(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)
    row = {"ts": datetime.now(timezone.utc).isoformat(), **record}
    with (logs_dir / "guardrail.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_guardrail.py -v`
Expected: 10 passed. `test_null_fault_party_assertion_fails`가 실패하면 "고객님이 부담하셔야" 문장이 `fault_party` 첫 패턴에 걸리는지 `re.search`로 직접 확인하고 패턴을 조정한다.

- [ ] **Step 5: Commit**

```bash
git add server/guardrail.py tests/test_guardrail.py
git commit -m "feat: 가드레일 - 숫자 출처 역추적과 미확정값 확답 검사

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: prompts.py와 router.py — 분류 그래프

**Files:**
- Create: `server/prompts.py`, `server/router.py`
- Test: `tests/test_router.py`

**Interfaces:**
- Consumes: `Domain`, `Settings.conf_threshold`, `Settings.router_model`
- Produces:
  - `prompts.build_route_guide(domain: Domain) -> str`
  - `prompts.build_answer_rules(domain: Domain) -> str`
  - `router.RouteDecision(BaseModel)`: `route: Literal[5종]`, `confidence: float`, `reason: str`
  - `router.build_router(domain: Domain, conf_threshold: float, classify: Callable[[str], RouteDecision] | None = None, model: str | None = None)` → 컴파일된 그래프. `classify`가 없으면 `model`로 LLM 분류기를 만든다.
  - 그래프 입력 `{"question": str}`, 출력에 `route, confidence, reason, action("HANDLE"|"ESCALATE"|"OUT_OF_SCOPE"), message`.
  - `router.make_llm_classifier(domain: Domain, model: str) -> Callable[[str], RouteDecision]`
  - `router.make_rule_classifier() -> Callable[[str], RouteDecision]` (평가 기준선용)

- [ ] **Step 1: 실패하는 테스트**

`tests/test_router.py`:

```python
import pytest
from server.domain import load_domain
from server.router import RouteDecision, build_router, make_rule_classifier
from server.prompts import build_route_guide, build_answer_rules


@pytest.fixture
def domain(modumall_dir):
    return load_domain(modumall_dir)


def fixed(route, conf):
    return lambda q: RouteDecision(route=route, confidence=conf, reason="테스트")


def test_handle_when_confident(domain):
    g = build_router(domain, 0.5, classify=fixed("SHIPPING", 0.9))
    out = g.invoke({"question": "배송비 얼마예요?"})
    assert out["route"] == "SHIPPING"
    assert out["action"] == "HANDLE"
    assert out["message"] is None


def test_escalate_below_threshold(domain):
    g = build_router(domain, 0.5, classify=fixed("SHIPPING", 0.49))
    out = g.invoke({"question": "그거요"})
    assert out["action"] == "ESCALATE"
    assert out["message"] == domain.escalate_message


def test_threshold_boundary_is_inclusive(domain):
    g = build_router(domain, 0.5, classify=fixed("SHIPPING", 0.5))
    assert g.invoke({"question": "x"})["action"] == "HANDLE"


def test_other_is_out_of_scope(domain):
    g = build_router(domain, 0.5, classify=fixed("OTHER", 0.95))
    out = g.invoke({"question": "홍대점 몇 시까지 해요?"})
    assert out["action"] == "OUT_OF_SCOPE"
    assert out["message"] == domain.out_of_scope_message


def test_route_decision_rejects_unknown_route():
    with pytest.raises(Exception):
        RouteDecision(route="배송문의", confidence=0.9, reason="x")


def test_rule_classifier_baseline():
    c = make_rule_classifier()
    assert c("환불 언제 되나요?").route == "RETURN_REFUND"
    assert c("배송비 얼마예요?").route == "SHIPPING"
    assert c("홍대 매장 어디예요?").route == "OTHER"
    assert c("ㅇㅇ").confidence < 0.5


def test_route_guide_contains_domain_definitions(domain):
    guide = build_route_guide(domain)
    for r, d in domain.routes.items():
        assert r in guide and d.definition in guide
    assert "0.5 미만" in guide


def test_answer_rules_mention_procedure(domain):
    rules = build_answer_rules(domain)
    assert "search_product" in rules
    assert "null" in rules
    assert domain.name in rules
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_router.py -v`
Expected: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: prompts.py**

```python
# -*- coding: utf-8 -*-
"""모델에게 주는 글. 이 파일이 성능에 가장 크게 영향을 준다.

도메인마다 다른 부분(라우트 정의, 분류 규칙, 쇼핑몰 이름)은 Domain 에서 채운다.
고친 뒤에는 반드시 eval 스크립트로 다시 재고, 표현만 살짝 다른 입력 서너 개로 흔들어 볼 것.
"""
from server.domain import Domain


def build_route_guide(domain: Domain) -> str:
    route_lines = "\n".join(f"- {name:<14}: {r.definition}" for name, r in domain.routes.items())
    return f"""\
너는 온라인 쇼핑몰 '{domain.name}'의 고객상담 라우터다. 고객 문의 한 건을 읽고 아래 5개 라우트 중
하나로 분류한다.

[라우트 정의]
{route_lines}

{domain.routing_rules}

[확신도 지침]
두 라우트 사이에서 결정하기 어려우면 confidence 를 0.5 미만으로 낮춰라. 애매한 것을 확신 있게
답하는 것보다, 애매하다고 밝히는 것이 이 시스템에서는 더 좋은 판단이다.
"""


def build_answer_rules(domain: Domain) -> str:
    return f"""\
너는 온라인 쇼핑몰 '{domain.name}'의 전화 상담원이다. 아래 [업무 매뉴얼]과 [조회 결과]에만 근거해
답변한다. 두 곳에 없는 내용은 만들어내지 않는다. 답변은 전화로 읽히므로 짧고 말하듯이 쓴다.

[답하기 전에 반드시 이 순서를 따른다]
1단계. 고객이 상품을 이름으로 말했으면 search_product 를 부른다. 상품 ID(P0000 형태)를 이미
       알고 있을 때만 건너뛴다.
2단계. search_product 결과에 resolved_product_id 가 있으면 그 ID로 필요한 조회 도구를 부른다.
       ambiguous 가 true 이거나 후보가 비어 있으면 조회하지 말고 어느 상품인지 되묻는다.
3단계. 주문·반품 이야기가 나오면 주문번호(O-0000)로 해당 조회 도구를 부른다. 주문번호를 모르면 되묻는다.
4단계. 조회를 마친 뒤에 답변을 쓴다. 도구를 부를 수 있는데도 부르지 않고 되묻는 것은 실패다.

절대 규칙
1. 매뉴얼 본문에 금액·기간이 그대로 적힌 값(기본 배송비, 반품 배송비, 출고 마감, 처리 소요기간,
   지정 택배사)은 그 값으로 바로 답해도 된다.
2. 매뉴얼에 [어드민 조회] 로 표시된 항목(무료배송 기준액, 상품 구성·소재·재고, 당일배송 제공 여부,
   개별 구매 가능 여부, 재입고일, 제작 소요기간, 주문·반품 진행 상태)은 [조회 결과] 에 있는 값만
   사용한다. 조회 결과에 없는 숫자를 답변에 쓰지 않는다.
3. 주문·반품의 진행 상태는 조회 결과 없이 단정하지 않는다.
4. 조회 결과의 값이 null 인 항목은 "아직 확정되지 않았다"로 답한다. 특히 검품 전에는 배송비 부담
   주체를 단정하지 않는다. is_confirmed 가 false 면 예정일을 말하지 않는다.
5. 상품도 주문도 특정할 수 없으면 되묻는다. 단, 매뉴얼의 고정값은 되물으면서도 함께 안내한다.
6. 존댓말로 간결하게 1~3문장. 불가한 사항은 사유를 먼저 밝히고 대안을 제시한다.
7. 숫자는 "2,500원"처럼 원 단위까지 붙여 읽기 쉽게 쓴다. 괄호·기호·목록 표시를 쓰지 않는다.
"""
```

- [ ] **Step 4: router.py**

```python
# -*- coding: utf-8 -*-
"""의도 분류 라우터. 노드 둘로 된 그래프다.

classify 는 모델이 하는 일(분류), gate 는 정책이 정하는 일(처리/이관/범위밖).
둘을 나눠 둔 덕에 임계값만 바꿀 때 모델을 다시 부르지 않아도 되고, classify 를
가짜 함수로 바꿔 그래프 흐름을 LLM 없이 시험할 수 있다.
"""
import re
from typing import Callable, Literal, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from server.domain import Domain
from server.prompts import build_route_guide

Route = Literal["ORDER_PLACE", "PRODUCT_INFO", "SHIPPING", "RETURN_REFUND", "OTHER"]


class RouteDecision(BaseModel):
    """고객 문의 한 건에 대한 라우팅 판단 결과."""
    route: Route = Field(description="문의를 배정할 라우트. 5개 값 중 하나만 사용한다.")
    confidence: float = Field(ge=0.0, le=1.0,
                              description="판단의 확신도. 두 라우트 사이에서 애매하면 0.5 미만으로 낮춘다.")
    reason: str = Field(description="그 라우트로 판단한 근거를 한 문장으로. 고객이 원하는 결과를 기준으로 쓴다.")


class RouterState(TypedDict, total=False):
    question: str
    route: str
    confidence: float
    reason: str
    action: str            # HANDLE / ESCALATE / OUT_OF_SCOPE
    message: Optional[str]


RULES = [
    (r"환불|반품|교환|취소|반송|수거|검품", "RETURN_REFUND"),
    (r"배송비|택배비|무료\s?배송|배송|택배|출고|도착|언제\s?(오|와)|어디쯤", "SHIPPING"),
    (r"주문할|구매할게|살게요|사고\s?싶|결제|주문\s?가능|구매\s?가능|낱개|따로|단품", "ORDER_PLACE"),
    (r"구성|포함|소재|재질|사이즈|치수|재고|정품|색상|품질|몇\s?(장|개)", "PRODUCT_INFO"),
    (r"매장|오프라인|지점|영업\s?시간|주차", "OTHER"),
]


def make_rule_classifier() -> Callable[[str], RouteDecision]:
    """키워드 규칙 분류기. 기준선(baseline) 측정용."""
    def classify(question: str) -> RouteDecision:
        for pattern, route in RULES:
            if re.search(pattern, question):
                return RouteDecision(route=route, confidence=0.8, reason="키워드 규칙 매치")
        return RouteDecision(route="OTHER", confidence=0.3, reason="매치되는 규칙 없음")
    return classify


def make_llm_classifier(domain: Domain, model: str) -> Callable[[str], RouteDecision]:
    from langchain.chat_models import init_chat_model
    guide = build_route_guide(domain)
    chain = init_chat_model(model, temperature=0, timeout=60, max_retries=2) \
        .with_structured_output(RouteDecision)

    def classify(question: str) -> RouteDecision:
        return chain.invoke([("system", guide), ("human", f"고객 문의: {question}")])
    return classify


def build_router(domain: Domain, conf_threshold: float,
                 classify: Optional[Callable[[str], RouteDecision]] = None,
                 model: Optional[str] = None):
    if classify is None:
        if model is None:
            raise ValueError("classify 또는 model 중 하나는 있어야 합니다")
        classify = make_llm_classifier(domain, model)

    def node_classify(state: RouterState) -> RouterState:
        d = classify(state["question"])
        return {"route": d.route, "confidence": d.confidence, "reason": d.reason}

    def node_gate(state: RouterState) -> RouterState:
        if state["confidence"] < conf_threshold:
            return {"action": "ESCALATE", "message": domain.escalate_message}
        if state["route"] == "OTHER":
            return {"action": "OUT_OF_SCOPE", "message": domain.out_of_scope_message}
        return {"action": "HANDLE", "message": None}

    g = StateGraph(RouterState)
    g.add_node("classify", node_classify)
    g.add_node("gate", node_gate)
    g.add_edge(START, "classify")
    g.add_edge("classify", "gate")
    g.add_edge("gate", END)
    return g.compile()
```

- [ ] **Step 5: 통과 확인**

Run: `.venv/bin/pytest tests/test_router.py -v`
Expected: 8 passed

- [ ] **Step 6: Commit**

```bash
git add server/prompts.py server/router.py tests/test_router.py
git commit -m "feat: 프롬프트 조립과 분류 라우터 그래프

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: answer.py — 도구 호출 루프

**Files:**
- Create: `server/answer.py`
- Test: `tests/test_answer.py`

**Interfaces:**
- Consumes: `make_tools`, `build_context`, `build_answer_rules`, `Settings.answer_model`, `Settings.max_tool_turns`
- Produces:
  - `build_answer_prompt(domain, route, tool_results: dict | None = None) -> str`
  - `Answerer` 클래스: `Answerer(domain, model: str | None = None, llm=None, max_tool_turns=3)`. `llm`은 `bind_tools` 완료된 러너블(테스트 주입용). `answer(question: str, route: str, history: list[str] | None = None) -> tuple[str, dict, list[dict]]` — (답변 텍스트, `{tool_name: result}`, `[{"name", "args"}]` 호출 순서 목록). 재귀 상한 초과 시 `(domain.escalate_message, {}, calls)`.

- [ ] **Step 1: 실패하는 테스트**

`tests/test_answer.py`:

```python
import json
import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from server.domain import load_domain
from server.answer import Answerer, build_answer_prompt


@pytest.fixture
def domain(modumall_dir):
    return load_domain(modumall_dir)


def scripted_llm(script):
    """호출될 때마다 script 의 다음 AIMessage 를 돌려주는 가짜 LLM."""
    it = iter(script)
    return RunnableLambda(lambda messages: next(it))


def test_prompt_contains_rules_context_and_results(domain):
    p = build_answer_prompt(domain, "SHIPPING", {"get_shipping_policy": {"shipping_fee": 2500}})
    assert "search_product" in p
    assert "## 4. 배송 문의" in p
    assert "## 6." not in p
    assert '"shipping_fee": 2500' in p


def test_tool_loop_calls_tool_then_answers(domain):
    llm = scripted_llm([
        AIMessage(content="", tool_calls=[{"name": "get_shipping_policy", "id": "c1",
                                           "args": {"product_id": "P4001", "order_amount": 59000}}]),
        AIMessage(content="무료배송 기준은 100,000원이라 41,000원이 부족합니다."),
    ])
    a = Answerer(domain, llm=llm, max_tool_turns=3)
    text, results, calls = a.answer("P4001 59000원어치 무료배송 되나요?", "SHIPPING")
    assert "41,000" in text
    assert results["get_shipping_policy"]["shortfall"] == 41000
    assert calls == [{"name": "get_shipping_policy", "args": {"product_id": "P4001", "order_amount": 59000}}]


def test_no_tool_call_returns_empty_results(domain):
    llm = scripted_llm([AIMessage(content="어떤 상품인지 말씀해 주시겠어요?")])
    text, results, calls = a = Answerer(domain, llm=llm).answer("배송비 얼마예요?", "SHIPPING")
    assert results == {} and calls == []
    assert text.endswith("?")


def test_recursion_limit_escalates(domain):
    forever = AIMessage(content="", tool_calls=[{"name": "search_product", "id": "x", "args": {"query": "세트"}}])
    llm = RunnableLambda(lambda m: forever)
    text, results, calls = Answerer(domain, llm=llm, max_tool_turns=2).answer("세트", "PRODUCT_INFO")
    assert text == domain.escalate_message
    assert results == {}


def test_history_is_prepended(domain):
    seen = {}
    def capture(messages):
        seen["human"] = [m for m in messages if getattr(m, "type", "") == "human" or (isinstance(m, tuple) and m[0] == "human")]
        return AIMessage(content="네.")
    a = Answerer(domain, llm=RunnableLambda(capture))
    a.answer("그거 배송비는요?", "SHIPPING", history=["캔버스화 살 건데요"])
    human = seen["human"][0]
    content = human.content if hasattr(human, "content") else human[1]
    assert "캔버스화 살 건데요" in content and "그거 배송비는요?" in content
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_answer.py -v`
Expected: FAIL, `ModuleNotFoundError: server.answer`

- [ ] **Step 3: 구현**

`server/answer.py`:

```python
# -*- coding: utf-8 -*-
"""답변 생성. 모델이 스스로 도구를 골라 부르는 그래프다.

agent 노드가 도구를 요청하면 tools 노드로 갔다가 다시 agent 로 돌아온다.
돌아오는 화살표가 곧 루프이고, 상한은 recursion_limit 으로 건다.
"""
import json
from typing import Annotated, Optional, TypedDict

from langchain.tools import tool
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from server.context import build_context
from server.domain import Domain
from server.prompts import build_answer_rules
from server.tools import make_tools


def build_answer_prompt(domain: Domain, route: str, tool_results: Optional[dict] = None) -> str:
    ctx = build_context(domain, route)
    tr = json.dumps(tool_results or {}, ensure_ascii=False, indent=1)
    return (f"{build_answer_rules(domain)}\n"
            f"===== 업무 매뉴얼 (라우트: {route}) =====\n{ctx}\n\n"
            f"===== 조회 결과 =====\n{tr}\n")


class ToolState(TypedDict, total=False):
    messages: Annotated[list, add_messages]


class Answerer:
    def __init__(self, domain: Domain, model: Optional[str] = None, llm=None, max_tool_turns: int = 3):
        self.domain = domain
        self.max_tool_turns = max_tool_turns
        self.tools = make_tools(domain)
        self.lc_tools = [tool(fn) for fn in self.tools.values()]
        if llm is None:
            if model is None:
                raise ValueError("model 또는 llm 중 하나는 있어야 합니다")
            from langchain.chat_models import init_chat_model
            llm = init_chat_model(model, temperature=0, timeout=60, max_retries=2).bind_tools(self.lc_tools)
        self.llm = llm
        self.graph = self._build()

    def _build(self):
        def agent(state: ToolState) -> ToolState:
            return {"messages": [self.llm.invoke(state["messages"])]}

        g = StateGraph(ToolState)
        g.add_node("agent", agent)
        g.add_node("tools", ToolNode(self.lc_tools))
        g.add_edge(START, "agent")
        g.add_conditional_edges("agent", tools_condition)
        g.add_edge("tools", "agent")
        return g.compile()

    def answer(self, question: str, route: str, history: Optional[list[str]] = None):
        """(답변 텍스트, {도구명: 결과}, [{"name", "args"}] 호출 순서) 를 돌려준다."""
        full_q = " ".join((history or []) + [question])
        init = {"messages": [("system", build_answer_prompt(self.domain, route)), ("human", full_q)]}
        calls: list[dict] = []
        try:
            out = self.graph.invoke(init, {"recursion_limit": 2 * self.max_tool_turns + 1})
        except GraphRecursionError:
            return self.domain.escalate_message, {}, calls
        results: dict = {}
        for m in out["messages"]:
            for tc in getattr(m, "tool_calls", None) or []:
                calls.append({"name": tc["name"], "args": tc["args"]})
            if getattr(m, "name", None) in self.tools:
                try:
                    results[m.name] = json.loads(m.content)
                except (json.JSONDecodeError, TypeError):
                    results[m.name] = m.content
        return out["messages"][-1].content, results, calls
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_answer.py -v`
Expected: 5 passed. `test_recursion_limit_escalates`가 통과하지 않고 무한 루프 조짐이 보이면 `recursion_limit` 값이 그래프 invoke 두 번째 인자에 들어갔는지 확인한다. `ToolNode`가 결과를 문자열이 아닌 형태로 주면 `json.loads` 대신 `m.content`가 이미 dict인지 분기한다.

- [ ] **Step 5: Commit**

```bash
git add server/answer.py tests/test_answer.py
git commit -m "feat: 도구 호출 루프 답변 생성기

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: pipeline.py — 전체 그래프와 체크포인터

**Files:**
- Create: `server/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `build_router`, `Answerer`, `guardrail.check`, `guardrail.log_violation`, `Domain`, `Settings`
- Produces:
  - `TurnResult` 데이터클래스: `answer: str`, `route: str | None`, `confidence: float | None`, `action: str`, `tools: list[dict]`, `guardrail: dict | None`, `elapsed_ms: int`, `end_call: bool`. `to_dict()`.
  - `Pipeline(domain, settings, router=None, answerer=None)` — `router`는 컴파일된 라우터 그래프, `answerer`는 `Answerer` 호환 객체(`answer(q, route, history)`). 없으면 settings의 모델로 만든다.
  - `Pipeline.start_call() -> str` (call_id)
  - `Pipeline.turn(call_id: str, text: str) -> TurnResult`. 알 수 없는 call_id면 `KeyError`.
  - action 값: `ANSWER`, `ASK`, `ESCALATE`, `OUT_OF_SCOPE`

- [ ] **Step 1: 실패하는 테스트**

`tests/test_pipeline.py`:

```python
import pytest
from server.config import Settings
from server.domain import load_domain
from server.router import RouteDecision, build_router
from server.pipeline import Pipeline


@pytest.fixture
def domain(modumall_dir):
    return load_domain(modumall_dir)


@pytest.fixture
def settings(tmp_path, modumall_dir):
    return Settings(router_model="x", answer_model="x", conf_threshold=0.5, max_tool_turns=3,
                    guardrail_retry=1, domain="modumall", domains_root=modumall_dir.parent,
                    logs_dir=tmp_path / "logs")


class FakeAnswerer:
    def __init__(self, script):
        self.script = list(script)   # [(text, results, calls), ...]
        self.questions = []

    def answer(self, question, route, history=None):
        self.questions.append((question, history or []))
        return self.script.pop(0)


def router_with(domain, route, conf):
    return build_router(domain, 0.5, classify=lambda q: RouteDecision(route=route, confidence=conf, reason="t"))


def test_answer_path(domain, settings):
    ans = FakeAnswerer([("기준은 100,000원이라 41,000원이 부족합니다.",
                         {"get_shipping_policy": {"free_shipping_threshold": 100000, "shortfall": 41000}},
                         [{"name": "get_shipping_policy", "args": {"product_id": "P4001"}}])])
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.9), answerer=ans)
    cid = p.start_call()
    r = p.turn(cid, "P4001 무료배송 되나요?")
    assert r.action == "ANSWER" and r.route == "SHIPPING" and r.guardrail["ok"] and not r.end_call
    assert r.tools[0]["name"] == "get_shipping_policy"


def test_escalate_path_ends_call(domain, settings):
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.2), answerer=FakeAnswerer([]))
    r = p.turn(p.start_call(), "그거요")
    assert r.action == "ESCALATE" and r.end_call and r.answer == domain.escalate_message


def test_out_of_scope_path_ends_call(domain, settings):
    p = Pipeline(domain, settings, router=router_with(domain, "OTHER", 0.9), answerer=FakeAnswerer([]))
    r = p.turn(p.start_call(), "홍대점 몇 시까지 해요?")
    assert r.action == "OUT_OF_SCOPE" and r.end_call and r.answer == domain.out_of_scope_message


def test_ask_path_when_no_tools(domain, settings):
    ans = FakeAnswerer([("어떤 상품인지 말씀해 주시겠어요?", {}, [])])
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.9), answerer=ans)
    r = p.turn(p.start_call(), "배송비 얼마예요?")
    assert r.action == "ASK" and not r.end_call and r.guardrail is None


def test_external_channel_order_is_out_of_scope(domain, settings):
    ans = FakeAnswerer([("확인했습니다.", {"get_order_status": {"is_external_channel": True}}, [])])
    p = Pipeline(domain, settings, router=router_with(domain, "RETURN_REFUND", 0.9), answerer=ans)
    r = p.turn(p.start_call(), "O-1009 반품이요")
    assert r.action == "OUT_OF_SCOPE" and r.end_call


def test_guardrail_retry_then_escalate(domain, settings):
    bad = ("무료배송 기준은 40,000원 이상입니다.", {"get_shipping_policy": {"free_shipping_threshold": 100000}}, [])
    ans = FakeAnswerer([bad, bad])
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.9), answerer=ans)
    r = p.turn(p.start_call(), "P4001 무료배송?")
    assert len(ans.questions) == 2
    assert r.action == "ESCALATE" and r.end_call
    assert (settings.logs_dir / "guardrail.jsonl").exists()


def test_guardrail_retry_succeeds(domain, settings):
    bad = ("무료배송 기준은 40,000원 이상입니다.", {"get_shipping_policy": {"free_shipping_threshold": 100000}}, [])
    good = ("무료배송 기준은 100,000원 이상입니다.", {"get_shipping_policy": {"free_shipping_threshold": 100000}}, [])
    ans = FakeAnswerer([bad, good])
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.9), answerer=ans)
    r = p.turn(p.start_call(), "P4001 무료배송?")
    assert r.action == "ANSWER" and r.guardrail["ok"]


def test_history_carries_across_turns(domain, settings):
    ans = FakeAnswerer([("어떤 상품인가요?", {}, []), ("네 확인했습니다.", {"get_product_detail": {}}, [])])
    p = Pipeline(domain, settings, router=router_with(domain, "PRODUCT_INFO", 0.9), answerer=ans)
    cid = p.start_call()
    p.turn(cid, "소재가 뭐예요?")
    p.turn(cid, "캔버스화요")
    assert ans.questions[1] == ("캔버스화요", ["소재가 뭐예요?"])


def test_unknown_call_id(domain, settings):
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.9), answerer=FakeAnswerer([]))
    with pytest.raises(KeyError):
        p.turn("nope", "x")
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_pipeline.py -v`
Expected: FAIL, `ModuleNotFoundError: server.pipeline`

- [ ] **Step 3: 구현**

`server/pipeline.py`:

```python
# -*- coding: utf-8 -*-
"""라우터 · 조회 · 생성 · 가드레일을 하나의 그래프로 잇는다.

route → answer → guard → (END | answer 재시도 | escalate)
통화(call_id)마다 체크포인터가 State 를 보존하므로 앞 턴의 발화가 뒤 턴에 이어진다.
"""
import operator
import re
import time
import uuid
from dataclasses import dataclass, asdict
from typing import Annotated, Optional, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from server import guardrail
from server.config import Settings
from server.domain import Domain

ASK_PATTERN = r"\?|주시겠|알려주|말씀해"


class AgentState(TypedDict, total=False):
    question: str
    history: Annotated[list, operator.add]   # 리듀서: 턴마다 쌓인다
    route: str
    confidence: float
    action: str        # HANDLE / ASK / ANSWER / RETRY / ESCALATE / OUT_OF_SCOPE
    tools: list
    results: dict
    answer: str
    guardrail: Optional[dict]
    attempts: int


@dataclass
class TurnResult:
    answer: str
    route: Optional[str]
    confidence: Optional[float]
    action: str
    tools: list
    guardrail: Optional[dict]
    elapsed_ms: int
    end_call: bool

    def to_dict(self) -> dict:
        return asdict(self)


class Pipeline:
    def __init__(self, domain: Domain, settings: Settings, router=None, answerer=None):
        self.domain = domain
        self.settings = settings
        if router is None:
            from server.router import build_router
            router = build_router(domain, settings.conf_threshold, model=settings.router_model)
        if answerer is None:
            from server.answer import Answerer
            answerer = Answerer(domain, model=settings.answer_model, max_tool_turns=settings.max_tool_turns)
        self.router = router
        self.answerer = answerer
        self.calls: set[str] = set()
        self._current_call: Optional[str] = None   # 단일 스레드 가정. 동시 통화는 범위 밖
        self.graph = self._build()

    # ── 노드 ──────────────────────────────────────────────
    def _node_route(self, state: AgentState) -> AgentState:
        q = " ".join((state.get("history") or []) + [state["question"]])
        r = self.router.invoke({"question": q})
        return {"route": r["route"], "confidence": r["confidence"], "action": r["action"],
                "attempts": 0, "tools": [], "results": {}, "guardrail": None}

    def _node_answer(self, state: AgentState) -> AgentState:
        text, results, calls = self.answerer.answer(state["question"], state["route"],
                                                    history=state.get("history") or [])
        attempts = state.get("attempts", 0) + 1
        if results.get("get_order_status", {}).get("is_external_channel"):
            return {"action": "OUT_OF_SCOPE", "tools": calls, "results": results, "attempts": attempts}
        if not results and re.search(ASK_PATTERN, text):
            return {"action": "ASK", "tools": calls, "results": {}, "answer": text, "attempts": attempts}
        return {"action": "HANDLE", "tools": calls, "results": results, "answer": text, "attempts": attempts}

    def _node_guard(self, state: AgentState) -> AgentState:
        g = guardrail.check(state["answer"], state["results"], self.domain)
        if not g.ok:
            for v in g.violations:
                guardrail.log_violation(self.settings.logs_dir, {
                    "call_id": self._current_call, "route": state["route"], "type": v["type"],
                    "detail": v["detail"], "answer": state["answer"]})
        return {"guardrail": g.to_dict(), "action": "ANSWER" if g.ok else "RETRY"}

    def _node_escalate(self, state: AgentState) -> AgentState:
        if state["action"] == "OUT_OF_SCOPE":
            return {"answer": self.domain.out_of_scope_message}
        return {"answer": self.domain.escalate_message, "action": "ESCALATE"}

    # ── 분기 ──────────────────────────────────────────────
    def _after_route(self, state: AgentState) -> str:
        return "answer" if state["action"] == "HANDLE" else "escalate"

    def _after_answer(self, state: AgentState) -> str:
        if state["action"] == "ASK":
            return END
        return "escalate" if state["action"] == "OUT_OF_SCOPE" else "guard"

    def _after_guard(self, state: AgentState) -> str:
        if state["action"] == "ANSWER":
            return END
        return "answer" if state["attempts"] <= self.settings.guardrail_retry else "escalate"

    def _build(self):
        g = StateGraph(AgentState)
        g.add_node("route", self._node_route)
        g.add_node("answer", self._node_answer)
        g.add_node("guard", self._node_guard)
        g.add_node("escalate", self._node_escalate)
        g.add_edge(START, "route")
        g.add_conditional_edges("route", self._after_route, {"answer": "answer", "escalate": "escalate"})
        g.add_conditional_edges("answer", self._after_answer, {"guard": "guard", "escalate": "escalate", END: END})
        g.add_conditional_edges("guard", self._after_guard, {"answer": "answer", "escalate": "escalate", END: END})
        g.add_edge("escalate", END)
        return g.compile(checkpointer=InMemorySaver())

    # ── 공개 API ──────────────────────────────────────────
    def start_call(self) -> str:
        cid = uuid.uuid4().hex[:12]
        self.calls.add(cid)
        return cid

    def turn(self, call_id: str, text: str) -> TurnResult:
        if call_id not in self.calls:
            raise KeyError(call_id)
        t0 = time.perf_counter()
        cfg = {"configurable": {"thread_id": call_id}}
        self._current_call = call_id
        out = self.graph.invoke({"question": text}, cfg)
        # 이번 발화를 history 에 쌓는다 (다음 턴에서 앞 문장으로 쓰인다)
        self.graph.update_state(cfg, {"history": [text]})
        action = out["action"]
        end = action in ("ESCALATE", "OUT_OF_SCOPE")
        return TurnResult(answer=out["answer"], route=out.get("route"), confidence=out.get("confidence"),
                          action=action, tools=out.get("tools") or [], guardrail=out.get("guardrail"),
                          elapsed_ms=int((time.perf_counter() - t0) * 1000), end_call=end)
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_pipeline.py -v`
Expected: 9 passed. `test_history_carries_across_turns`가 실패하면 `update_state` 대신 `_node_answer`/`_node_escalate` 반환값에 `"history": [state["question"]]`를 넣는 방식으로 바꾼다(둘 중 하나만 써야 두 번 쌓이지 않는다).

- [ ] **Step 5: Commit**

```bash
git add server/pipeline.py tests/test_pipeline.py
git commit -m "feat: 전체 파이프라인 그래프와 통화별 체크포인터

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: app.py — FastAPI

**Files:**
- Create: `server/app.py`, `server/__main__.py`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `Pipeline`, `Domain`, `load_settings`, `load_domain`
- Produces:
  - `create_app(pipeline, domain) -> FastAPI`
  - 엔드포인트: `GET /` (web/index.html), `GET /api/domain`, `POST /api/call/start`, `POST /api/call/turn`, 정적 `/static/*` → `web/`
  - `python -m server` 로 uvicorn 기동 (host 127.0.0.1, port 8000)

- [ ] **Step 1: 실패하는 테스트**

`tests/test_app.py`:

```python
import pytest
from fastapi.testclient import TestClient
from server.app import create_app
from server.domain import load_domain
from server.pipeline import TurnResult


class FakePipeline:
    def __init__(self):
        self.calls = set()

    def start_call(self):
        self.calls.add("abc")
        return "abc"

    def turn(self, call_id, text):
        if call_id not in self.calls:
            raise KeyError(call_id)
        return TurnResult(answer=f"응답:{text}", route="SHIPPING", confidence=0.9, action="ANSWER",
                          tools=[], guardrail={"ok": True, "violations": []}, elapsed_ms=5, end_call=False)


@pytest.fixture
def client(modumall_dir):
    return TestClient(create_app(FakePipeline(), load_domain(modumall_dir)))


def test_domain_endpoint(client):
    r = client.get("/api/domain")
    assert r.status_code == 200 and r.json()["name"] == "모두몰" and "greeting" in r.json()


def test_start_and_turn(client):
    s = client.post("/api/call/start").json()
    assert s["call_id"] == "abc" and s["greeting"]
    t = client.post("/api/call/turn", json={"call_id": "abc", "text": "배송비"}).json()
    assert t["answer"] == "응답:배송비" and t["action"] == "ANSWER" and t["end_call"] is False


def test_unknown_call_is_404(client):
    r = client.post("/api/call/turn", json={"call_id": "zzz", "text": "x"})
    assert r.status_code == 404


def test_empty_text_is_422(client):
    r = client.post("/api/call/turn", json={"call_id": "abc", "text": "  "})
    assert r.status_code == 422


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "<html" in r.text.lower()
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_app.py -v`
Expected: FAIL, `ModuleNotFoundError: server.app`

- [ ] **Step 3: 구현**

`server/app.py`:

```python
# -*- coding: utf-8 -*-
"""FastAPI 앱. 정적 화면을 서빙하고 통화 API 3개를 제공한다."""
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from server.domain import Domain

WEB = Path(__file__).resolve().parent.parent / "web"


class TurnRequest(BaseModel):
    call_id: str
    text: str

    @field_validator("text")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("text 가 비어 있습니다")
        return v.strip()


def create_app(pipeline, domain: Domain) -> FastAPI:
    app = FastAPI(title=f"{domain.name} 음성 상담 에이전트")

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html")

    @app.get("/api/domain")
    def get_domain():
        return {"name": domain.name, "greeting": domain.greeting}

    @app.post("/api/call/start")
    def start_call():
        return {"call_id": pipeline.start_call(), "greeting": domain.greeting}

    @app.post("/api/call/turn")
    def turn(req: TurnRequest):
        try:
            return pipeline.turn(req.call_id, req.text).to_dict()
        except KeyError:
            raise HTTPException(status_code=404, detail="알 수 없는 call_id 입니다")

    if WEB.exists():
        app.mount("/static", StaticFiles(directory=WEB), name="static")
    return app


def build_default_app() -> FastAPI:
    from server.config import load_settings
    from server.domain import load_domain
    from server.pipeline import Pipeline
    settings = load_settings()
    domain = load_domain(settings.domains_root / settings.domain)
    return create_app(Pipeline(domain, settings), domain)
```

`server/__main__.py`:

```python
import uvicorn
from server.app import build_default_app

if __name__ == "__main__":
    uvicorn.run(build_default_app(), host="127.0.0.1", port=8000)
```

`test_index_served`를 위해 임시 `web/index.html`을 만든다 (Task 10에서 교체):

```html
<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><title>준비 중</title></head><body>준비 중</body></html>
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_app.py -v`
Expected: 5 passed

- [ ] **Step 5: 전체 테스트**

Run: `.venv/bin/pytest -q`
Expected: 모두 통과 (약 54개)

- [ ] **Step 6: Commit**

```bash
git add server/app.py server/__main__.py web/index.html tests/test_app.py
git commit -m "feat: FastAPI 통화 API와 정적 화면 서빙

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: 화면 — index.html, voice.js, call.js, panel.js

**Files:**
- Modify: `web/index.html` (교체)
- Create: `web/voice.js`, `web/call.js`, `web/panel.js`, `web/style.css`

**Interfaces:**
- Consumes: API 3개 (Task 9)
- Produces: 브라우저 화면. 자동 테스트 없음. 수동 검증은 Step 6.

- [ ] **Step 1: voice.js**

```javascript
// 음성 인식·합성 모듈. listen / speak / stop 세 함수만 노출한다.
// Web Speech API (크롬·엣지). 서버 TTS로 바꿀 때는 이 파일만 교체한다.
export function createVoice({ lang = "ko-KR", onInterim = () => {} } = {}) {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const synth = window.speechSynthesis;
  let rec = null;
  let currentUtter = null;
  let voice = null;

  function pickVoice() {
    const all = synth ? synth.getVoices() : [];
    const ko = all.filter(v => v.lang && v.lang.toLowerCase().startsWith("ko"));
    return ko[0] || all[0] || null;
  }
  if (synth) {
    voice = pickVoice();
    synth.onvoiceschanged = () => { if (!voice) voice = pickVoice(); };
  }

  return {
    get supported() { return { recognition: !!SR, synthesis: !!synth }; },
    listVoices() { return synth ? synth.getVoices().filter(v => v.lang.toLowerCase().startsWith("ko")) : []; },
    setVoice(v) { voice = v; },

    listen() {
      return new Promise((resolve, reject) => {
        if (!SR) return reject(new Error("unsupported"));
        rec = new SR();
        rec.lang = lang;
        rec.interimResults = true;
        rec.continuous = false;
        rec.maxAlternatives = 1;
        let finalText = "";
        rec.onresult = (e) => {
          let interim = "";
          for (let i = e.resultIndex; i < e.results.length; i++) {
            const t = e.results[i][0].transcript;
            if (e.results[i].isFinal) finalText += t; else interim += t;
          }
          onInterim(interim || finalText);
        };
        rec.onerror = (e) => { rec = null; reject(new Error(e.error)); };
        rec.onend = () => { rec = null; resolve(finalText.trim()); };
        rec.start();
      });
    },

    speak(text) {
      return new Promise((resolve) => {
        if (!synth || !text) return resolve();
        synth.cancel();
        const u = new SpeechSynthesisUtterance(text);
        u.lang = lang;
        if (voice) u.voice = voice;
        u.rate = 1.0;
        u.onend = () => { currentUtter = null; resolve(); };
        u.onerror = () => { currentUtter = null; resolve(); };
        currentUtter = u;
        synth.speak(u);
      });
    },

    stop() {
      if (rec) { try { rec.abort(); } catch (_) {} rec = null; }
      if (synth) synth.cancel();
      currentUtter = null;
    },
  };
}
```

- [ ] **Step 2: panel.js**

```javascript
// 관리자 패널: 턴마다 카드 하나. 라우트·확신도·도구·가드레일·소요 시간.
export function createPanel(root) {
  const THRESHOLD = 0.5;
  function esc(s) { return String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

  return {
    clear() { root.innerHTML = ""; },
    addTurn(question, r) {
      const low = r.confidence != null && r.confidence < THRESHOLD;
      const bad = ["ESCALATE", "OUT_OF_SCOPE"].includes(r.action) || (r.guardrail && !r.guardrail.ok);
      const tools = (r.tools || []).map(t => `<li><code>${esc(t.name)}</code> ${esc(JSON.stringify(t.args))}</li>`).join("") || "<li class='muted'>호출 없음</li>";
      const guard = r.guardrail
        ? (r.guardrail.ok ? `<span class="ok">통과</span>` : `<span class="bad">위반</span> ${r.guardrail.violations.map(v => esc(v.type + ": " + v.detail)).join("<br>")}`)
        : `<span class="muted">검사 안 함</span>`;
      const card = document.createElement("article");
      card.className = "turn" + (bad ? " turn-bad" : "");
      card.innerHTML = `
        <div class="q">고객: ${esc(question)}</div>
        <div class="row"><b>라우트</b> ${esc(r.route || "-")} <span class="${low ? "bad" : ""}">conf ${r.confidence == null ? "-" : r.confidence.toFixed(2)}</span>
          <span class="badge ${bad ? "badge-bad" : ""}">${esc(r.action)}</span></div>
        <div class="row"><b>도구</b><ul>${tools}</ul></div>
        <div class="row"><b>가드레일</b> ${guard}</div>
        <div class="row muted">${r.elapsed_ms} ms</div>
        <div class="a">상담원: ${esc(r.answer)}</div>`;
      root.prepend(card);
    },
  };
}
```

- [ ] **Step 3: call.js**

```javascript
// 전화 상태 머신. IDLE → RINGING → SPEAKING ⇄ LISTENING → THINKING → ... → ENDED
import { createVoice } from "./voice.js";
import { createPanel } from "./panel.js";

const $ = (id) => document.getElementById(id);
const state = { phase: "IDLE", callId: null, startedAt: null, turns: 0, textOnly: false, timer: null };
const voice = createVoice({ onInterim: (t) => { $("interim").textContent = t; } });
const panel = createPanel($("panel"));

function setPhase(p) {
  state.phase = p;
  document.body.dataset.phase = p;
  $("status").textContent = { IDLE: "대기", RINGING: "연결 중…", SPEAKING: "상담원 말하는 중", LISTENING: "듣는 중 — 말씀하세요",
                              THINKING: "확인 중…", ENDED: "통화 종료" }[p];
}

function playRing(ms) {
  return new Promise((resolve) => {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator(); const gain = ctx.createGain();
    osc.type = "sine"; osc.frequency.value = 440; gain.gain.value = 0.08;
    osc.connect(gain).connect(ctx.destination); osc.start();
    let on = true;
    const iv = setInterval(() => { on = !on; gain.gain.value = on ? 0.08 : 0; }, 400);
    setTimeout(() => { clearInterval(iv); osc.stop(); ctx.close(); resolve(); }, ms);
  });
}

function addBubble(who, text) {
  const div = document.createElement("div");
  div.className = "bubble " + who;
  div.textContent = text;
  $("transcript").appendChild(div);
  $("transcript").scrollTop = $("transcript").scrollHeight;
}

async function startCall() {
  panel.clear(); $("transcript").innerHTML = ""; state.turns = 0;
  setPhase("RINGING");
  await playRing(1500);
  const r = await fetch("/api/call/start", { method: "POST" }).then(r => r.json());
  state.callId = r.call_id; state.startedAt = Date.now();
  state.timer = setInterval(() => {
    const s = Math.floor((Date.now() - state.startedAt) / 1000);
    $("clock").textContent = `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  }, 500);
  await say(r.greeting);
  listenLoop();
}

async function say(text) {
  setPhase("SPEAKING");
  addBubble("agent", text);
  if (!state.textOnly) await voice.speak(text);
}

async function listenLoop() {
  if (state.phase === "ENDED") return;
  if (state.textOnly) { setPhase("LISTENING"); return; }   // 텍스트 입력을 기다린다
  setPhase("LISTENING");
  let text = "";
  try { text = await voice.listen(); }
  catch (e) {
    if (e.message === "not-allowed" || e.message === "unsupported") { enableTextOnly("마이크를 쓸 수 없어 텍스트 입력으로 전환했습니다."); return; }
    // no-speech 등은 다시 듣는다
    return listenLoop();
  }
  $("interim").textContent = "";
  if (!text) return listenLoop();
  await sendTurn(text);
}

async function sendTurn(text) {
  if (state.phase === "ENDED" || !state.callId) return;
  addBubble("customer", text);
  setPhase("THINKING");
  const filler = setTimeout(() => { if (state.phase === "THINKING" && !state.textOnly) voice.speak("잠시만 확인해 드리겠습니다."); }, 1500);
  let r;
  try {
    const res = await fetch("/api/call/turn", { method: "POST", headers: { "Content-Type": "application/json" },
                                                body: JSON.stringify({ call_id: state.callId, text }) });
    if (!res.ok) throw new Error(`서버 오류 ${res.status}: ${await res.text()}`);
    r = await res.json();
  } catch (e) {
    clearTimeout(filler);
    addBubble("system", String(e.message));
    return listenLoop();
  }
  clearTimeout(filler);
  state.turns += 1;
  panel.addTurn(text, r);
  await say(r.answer);
  if (r.end_call) return endCall("에이전트가 통화를 종료했습니다");
  listenLoop();
}

function endCall(reason = "통화를 끊었습니다") {
  voice.stop();
  clearInterval(state.timer);
  setPhase("ENDED");
  addBubble("system", `${reason} · ${$("clock").textContent} · ${state.turns}턴`);
  state.callId = null;
}

function enableTextOnly(msg) {
  state.textOnly = true;
  document.body.classList.add("text-only");
  if (msg) addBubble("system", msg);
  setPhase("LISTENING");
}

// ── 이벤트 ──
$("btn-call").onclick = () => { if (state.phase === "IDLE" || state.phase === "ENDED") startCall(); };
$("btn-hangup").onclick = () => { if (state.phase !== "IDLE" && state.phase !== "ENDED") endCall(); };
$("text-form").onsubmit = (e) => {
  e.preventDefault();
  const t = $("text-input").value.trim();
  if (!t || !state.callId) return;
  $("text-input").value = "";
  voice.stop();
  sendTurn(t);
};
$("voice-select").onchange = (e) => { const v = voice.listVoices()[e.target.value]; if (v) voice.setVoice(v); };

// 초기화
(async () => {
  const d = await fetch("/api/domain").then(r => r.json());
  $("shop-name").textContent = d.name;
  if (!voice.supported.recognition) enableTextOnly("이 브라우저는 음성 인식을 지원하지 않습니다. 크롬 또는 엣지를 권장합니다.");
  const fill = () => { $("voice-select").innerHTML = voice.listVoices().map((v, i) => `<option value="${i}">${v.name}</option>`).join(""); };
  fill(); if (window.speechSynthesis) window.speechSynthesis.onvoiceschanged = fill;
  setPhase("IDLE");
})();
```

- [ ] **Step 4: index.html과 style.css**

`web/index.html`:

```html
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>음성 상담 에이전트</title>
<link rel="stylesheet" href="/static/style.css">
</head>
<body data-phase="IDLE">
<div class="layout">
  <section class="phone">
    <header>
      <div class="shop"><span id="shop-name">…</span> 고객센터</div>
      <div class="meta"><span id="status">대기</span> · <span id="clock">00:00</span></div>
    </header>
    <div id="transcript" class="transcript"></div>
    <div id="interim" class="interim"></div>
    <form id="text-form" class="text-form">
      <input id="text-input" type="text" placeholder="말하기 대신 입력하려면 여기에 쓰고 Enter" autocomplete="off">
    </form>
    <div class="controls">
      <button id="btn-call" class="call">📞 전화 걸기</button>
      <button id="btn-hangup" class="hangup">끊기</button>
      <label class="voice">목소리 <select id="voice-select"></select></label>
    </div>
  </section>
  <aside class="admin">
    <h2>관리자 패널</h2>
    <div id="panel"></div>
  </aside>
</div>
<script type="module" src="/static/call.js"></script>
</body>
</html>
```

`web/style.css`:

```css
:root { --bg:#111418; --paper:#1b2027; --ink:#e8e8e4; --muted:#8b93a0; --line:#2b323c; --ok:#4ade80; --bad:#f87171; --accent:#22c55e; }
* { box-sizing:border-box }
body { margin:0; background:var(--bg); color:var(--ink); font-family:"Noto Sans KR",system-ui,sans-serif; }
.layout { display:grid; grid-template-columns: 420px 1fr; height:100vh; }
.phone { display:flex; flex-direction:column; border-right:1px solid var(--line); background:var(--paper); }
.phone header { padding:16px 20px; border-bottom:1px solid var(--line); }
.shop { font-weight:700; font-size:1.1em }
.meta { color:var(--muted); font-size:.9em; margin-top:4px }
.transcript { flex:1; overflow-y:auto; padding:16px 20px; display:flex; flex-direction:column; gap:8px; }
.bubble { max-width:85%; padding:10px 14px; border-radius:14px; line-height:1.5; font-size:.95em }
.bubble.agent { background:#243042; align-self:flex-start }
.bubble.customer { background:#1f5d3a; align-self:flex-end }
.bubble.system { color:var(--muted); font-size:.85em; align-self:center; text-align:center }
.interim { min-height:1.6em; padding:0 20px; color:var(--muted); font-style:italic }
.text-form { padding:8px 20px }
.text-form input { width:100%; padding:10px 12px; border-radius:8px; border:1px solid var(--line); background:var(--bg); color:var(--ink) }
.controls { display:flex; gap:10px; align-items:center; padding:12px 20px 20px; border-top:1px solid var(--line) }
button { padding:12px 18px; border:none; border-radius:999px; font-size:1em; cursor:pointer; color:#fff }
.call { background:var(--accent) } .hangup { background:var(--bad) }
.voice { margin-left:auto; color:var(--muted); font-size:.85em }
.voice select { max-width:150px }
body[data-phase="IDLE"] .hangup, body[data-phase="ENDED"] .hangup { display:none }
body:not([data-phase="IDLE"]):not([data-phase="ENDED"]) .call { display:none }
body[data-phase="LISTENING"] header { box-shadow: inset 0 -3px 0 var(--accent) }
body[data-phase="THINKING"] header { box-shadow: inset 0 -3px 0 #facc15 }
body[data-phase="SPEAKING"] header { box-shadow: inset 0 -3px 0 #60a5fa }
.admin { overflow-y:auto; padding:20px 24px }
.admin h2 { margin:0 0 12px; font-size:1.05em; color:var(--muted) }
.turn { background:var(--paper); border:1px solid var(--line); border-radius:10px; padding:12px 14px; margin-bottom:10px; font-size:.9em }
.turn-bad { border-color:var(--bad) }
.turn .q { color:#a7f3d0; margin-bottom:6px } .turn .a { color:var(--muted); margin-top:6px; border-top:1px dashed var(--line); padding-top:6px }
.turn .row { margin:3px 0 } .turn ul { margin:2px 0 0 18px; padding:0 }
.badge { background:#334155; padding:1px 8px; border-radius:6px; font-size:.8em; margin-left:6px } .badge-bad { background:var(--bad) }
.ok { color:var(--ok) } .bad { color:var(--bad) } .muted { color:var(--muted) }
code { background:#0f1318; padding:1px 5px; border-radius:4px }
@media (max-width: 900px) { .layout { grid-template-columns:1fr; grid-template-rows: 1fr auto } .admin { max-height:40vh } }
```

- [ ] **Step 5: 앱 테스트 재확인**

Run: `.venv/bin/pytest tests/test_app.py -q`
Expected: 5 passed (index.html 교체 후에도 `<html` 포함)

- [ ] **Step 6: 수동 검증 (API 키 필요)**

```bash
cp .env.example .env   # OPENAI_API_KEY 채우기
.venv/bin/python -m server
```

크롬에서 http://127.0.0.1:8000 을 열고:
1. "전화 걸기" → 벨소리 1.5초 → 인사말이 음성으로 나온다.
2. 마이크 권한 허용 → "캔버스화 배송비 얼마예요?" 말하기 → 상태가 확인 중 → 답변 음성. 패널에 `search_product`, `get_shipping_policy`가 찍힌다.
3. 입력창에 "홍대점 몇 시까지 해요?" 입력 → 범위 밖 안내 후 통화 종료.
4. 마이크 권한 거부 후 새로고침 → 텍스트 전용 안내가 뜨고 입력창으로 대화된다.

문제가 있으면 브라우저 콘솔과 서버 로그를 함께 확인한다. 모델명 오류(`model_not_found`)가 나면 `.env`의 `ROUTER_MODEL`/`ANSWER_MODEL`을 사용 가능한 모델로 바꾼다.

- [ ] **Step 7: Commit**

```bash
git add web
git commit -m "feat: 전화 연출 화면 - 음성 인식·TTS·텍스트 대체·관리자 패널

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: 평가 — scoring.py, eval_router.py, eval_answer.py

**Files:**
- Create: `eval/scoring.py`, `eval/eval_router.py`, `eval/eval_answer.py`
- Test: `tests/test_scoring.py`

**Interfaces:**
- Consumes: `build_router`, `make_rule_classifier`, `make_llm_classifier`, `Answerer`, `load_domain`, `load_settings`
- Produces:
  - `scoring.score_turn(expect: dict, answer: str, tools_called: list[str], action: str) -> tuple[bool, list[str]]`
  - `scoring.infer_action(text: str, results: dict) -> str`
  - `scoring.load_first_turns(goldenset_path: Path) -> list[dict]` — `{conv_id, question, route, expect}`
  - `scoring.self_check(cases) -> list[str]` — 모범 답안이 실패한 conv_id 목록

- [ ] **Step 1: 실패하는 테스트**

`tests/test_scoring.py`:

```python
from eval.scoring import score_turn, infer_action, load_first_turns, self_check


def test_score_turn_passes_reference():
    expect = {"action": "ANSWER", "tools": ["get_shipping_policy"], "must": ["50000", "2500"], "forbid": ["40000"]}
    ok, fails = score_turn(expect, "가방·잡화는 기준 50,000원이라 배송비 2,500원이 발생합니다.", ["get_shipping_policy"], "ANSWER")
    assert ok, fails


def test_score_turn_detects_each_failure():
    expect = {"action": "ANSWER", "tools": ["get_shipping_policy"], "must": ["50000"], "forbid": ["40000"]}
    ok, fails = score_turn(expect, "기준은 40,000원입니다.", [], "ASK")
    assert not ok
    assert any(f.startswith("action") for f in fails)
    assert any("tools 미호출" in f for f in fails)
    assert any("must 누락" in f for f in fails)
    assert any("forbid 위반" in f for f in fails)


def test_ask_requires_question_form():
    ok, fails = score_turn({"action": "ASK", "must": [], "forbid": []}, "확인했습니다.", [], "ASK")
    assert not ok and any("되묻는" in f for f in fails)


def test_infer_action():
    assert infer_action("어떤 상품인가요?", {}) == "ASK"
    assert infer_action("확인했습니다.", {"get_order_status": {"is_external_channel": True}}) == "OUT_OF_SCOPE"
    assert infer_action("기준은 100,000원입니다.", {"get_shipping_policy": {}}) == "ANSWER"


def test_goldenset_self_check_all_pass(modumall_dir):
    cases = load_first_turns(modumall_dir / "eval" / "answer_goldenset.json")
    assert len(cases) == 34
    assert self_check(cases) == []
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_scoring.py -v`
Expected: FAIL, `ModuleNotFoundError: eval.scoring`

- [ ] **Step 3: scoring.py**

```python
# -*- coding: utf-8 -*-
"""정답셋 채점 규칙. LLM 없이 돈다."""
import json
import re
from pathlib import Path

ASK_PATTERN = r"\?|주시겠|알려주|말씀해"


def norm_num(s) -> str:
    return re.sub(r"(?<=\d),(?=\d)", "", str(s))


def score_turn(expect: dict, answer: str, tools_called: list, action: str):
    fails = []
    a = norm_num(answer)
    if expect["action"] != action:
        fails.append(f'action: 기대 {expect["action"]} != 실제 {action}')
    need = set(expect.get("tools", []))
    if need - set(tools_called):
        fails.append(f"tools 미호출: {sorted(need - set(tools_called))}")
    for m in expect.get("must", []):
        if norm_num(m) not in a:
            fails.append(f'must 누락: "{m}"')
    for f in expect.get("forbid", []):
        if norm_num(f) in a:
            fails.append(f'forbid 위반: "{f}"')
    if expect["action"] == "ASK" and not re.search(ASK_PATTERN, answer):
        fails.append("ASK 인데 되묻는 문장이 아님")
    return (not fails), fails


def infer_action(text: str, results: dict) -> str:
    if results.get("get_order_status", {}).get("is_external_channel"):
        return "OUT_OF_SCOPE"
    if not results and re.search(ASK_PATTERN, text):
        return "ASK"
    return "ANSWER"


def load_first_turns(goldenset_path: Path) -> list[dict]:
    gold = json.loads(Path(goldenset_path).read_text(encoding="utf-8"))
    cases = []
    for c in gold["conversations"]:
        q = next(t for t in c["turns"] if t["role"] == "customer")
        a = next((t for t in c["turns"] if t.get("expect")), None)
        if a:
            cases.append({"conv_id": c["conv_id"], "question": q["text"],
                          "route": c["route"].split("→")[-1].strip(), "expect": a["expect"]})
    return cases


def self_check(cases: list[dict]) -> list[str]:
    """모범 답안(reference)이 채점기를 통과하지 못하면 채점기가 틀린 것이다."""
    return [c["conv_id"] for c in cases
            if not score_turn(c["expect"], c["expect"]["reference"],
                              c["expect"].get("tools", []), c["expect"]["action"])[0]]
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_scoring.py -v`
Expected: 5 passed. `test_goldenset_self_check_all_pass`가 실패하면 실패한 conv_id의 `reference`와 `must`/`forbid`를 열어 보고, 채점기 규칙(콤마 정규화, ASK 패턴)이 모범 답안과 맞는지 확인한다. 정답셋 자체 문제면 해당 conv_id를 출력에 남기고 테스트를 `assert len(self_check(cases)) <= N`으로 완화하되 N과 이유를 주석에 적는다.

- [ ] **Step 5: eval_router.py**

```python
# -*- coding: utf-8 -*-
"""① 의도 분류 평가.  .venv/bin/python -m eval.eval_router [--limit N] [--rule] [--domain modumall]"""
import argparse
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score

from server.config import load_settings
from server.domain import ROUTES, load_domain
from server.router import build_router, make_llm_classifier, make_rule_classifier

LABELS4 = [r for r in ROUTES if r != "OTHER"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--rule", action="store_true", help="키워드 규칙 분류기로 기준선을 잰다")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    s = load_settings()
    domain = load_domain(s.domains_root / (args.domain or s.domain))
    ev_dir = domain.path / "eval"
    inq = pd.read_csv(ev_dir / "customer_inquiries.csv", encoding="utf-8-sig")
    ans = pd.read_csv(ev_dir / "routing_answers.csv", encoding="utf-8-sig")
    ev = inq.merge(ans, on="qa_id")
    ev = ev[ev["split"] == "eval"].reset_index(drop=True)
    if args.limit:
        ev = ev.head(args.limit)

    classify = make_rule_classifier() if args.rule else make_llm_classifier(domain, s.router_model)
    graph = build_router(domain, s.conf_threshold, classify=classify)
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        states = list(ex.map(lambda q: graph.invoke({"question": q}), ev["question"].tolist()))
    pred = [st["route"] for st in states]
    y = ev["route"].tolist()

    name = "규칙 라우터" if args.rule else f"LLM 라우터 ({s.router_model})"
    print(f"[{name}] n={len(ev)}  정확도 {accuracy_score(y, pred):.3f}  "
          f"macro F1 {f1_score(y, pred, labels=LABELS4, average='macro', zero_division=0):.3f}\n")
    print(classification_report(y, pred, labels=LABELS4, digits=3, zero_division=0))
    print("[혼동 행렬] 행=정답, 열=예측")
    print(pd.DataFrame(confusion_matrix(y, pred, labels=LABELS4), index=LABELS4, columns=LABELS4).to_string())
    miss = [(r["question"], r["route"], p, st["confidence"]) for (_, r), p, st in zip(ev.iterrows(), pred, states) if r["route"] != p]
    print(f"\n[오분류 {len(miss)}건] — 여기를 읽는 것이 개선의 출발점이다")
    for q, g, p, c in miss:
        print(f"  [{g} → {p}] conf={c:.2f}  {q[:60]}")
    esc = sum(1 for st in states if st["action"] == "ESCALATE")
    print(f"\n이관 {esc}건 / 자동화율 {(len(states) - esc) / len(states):.3f}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: eval_answer.py**

```python
# -*- coding: utf-8 -*-
"""② 1턴 답변 평가.  .venv/bin/python -m eval.eval_answer [--limit N] [--domain modumall]

라우트는 정답셋 값을 그대로 쓴다(라우터 실패와 섞지 않기 위해). 채점하는 것은 조회와 답변 생성이다.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from eval.scoring import infer_action, load_first_turns, score_turn, self_check
from server.answer import Answerer
from server.config import load_settings
from server.domain import load_domain

AUTO_ACTIONS = {"ANSWER", "ASK", "OUT_OF_SCOPE"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    s = load_settings()
    domain = load_domain(s.domains_root / (args.domain or s.domain))
    cases = load_first_turns(domain.path / "eval" / "answer_goldenset.json")
    bad = self_check(cases)
    print(f"[채점기 자기 검증] 모범 답안 {len(cases)}건 중 실패 {len(bad)}건 {bad if bad else '✅'}")
    if bad:
        raise SystemExit("채점기가 모범 답안을 통과시키지 못했습니다. 채점기부터 고치세요.")

    scored = [c for c in cases if c["expect"]["action"] in AUTO_ACTIONS]
    if args.limit:
        scored = scored[:args.limit]
    answerer = Answerer(domain, model=s.answer_model, max_tool_turns=s.max_tool_turns)

    def run(case):
        text, results, calls = answerer.answer(case["question"], case["route"])
        return infer_action(text, results), text, list(results)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        outs = list(ex.map(run, scored))

    rows = []
    for c, (action, text, tools) in zip(scored, outs):
        ok, fails = score_turn(c["expect"], text, tools, action)
        rows.append({"conv": c["conv_id"], "기대": c["expect"]["action"], "실제": action, "ok": ok,
                     "fails": "; ".join(fails), "answer": text})
    res = pd.DataFrame(rows)
    print(f'\n채점 {len(res)}건 / 통과 {int(res["ok"].sum())}건 ({100 * res["ok"].mean():.1f}%)'
          f'   (자동 판정 불가 {len(cases) - len(scored)}건 제외)')
    print("\n[행동 판정 혼동] 행=기대, 열=실제")
    print(pd.crosstab(res["기대"], res["실제"]).to_string())
    kinds = [f.split(":")[0] for s_ in res.loc[~res["ok"], "fails"] for f in s_.split("; ") if f]
    print("\n[실패 유형]")
    print(pd.Series(kinds).value_counts().to_string() if kinds else "  없음")
    print("\n[실패 사례] — 여기를 읽는 것이 개선의 출발점이다")
    for _, r in res[~res["ok"]].iterrows():
        print(f'  {r["conv"]} 기대={r["기대"]} 실제={r["실제"]}  {r["fails"][:90]}')
        print(f'      답변: {r["answer"][:100]}')


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: 규칙 기준선 실행 (API 키 불필요)**

Run: `.venv/bin/python -m eval.eval_router --rule`
Expected: 정확도·macro F1·혼동 행렬 출력, 오류 없음. macro F1은 0.6~0.7 근처.

- [ ] **Step 8: LLM 평가 소량 실행 (API 키 필요)**

Run: `.venv/bin/python -m eval.eval_router --limit 20` 그리고 `.venv/bin/python -m eval.eval_answer --limit 5`
Expected: 오류 없이 표 출력. 결과 숫자를 README 기록표에 적는다.

- [ ] **Step 9: Commit**

```bash
git add eval tests/test_scoring.py
git commit -m "feat: 라우팅 평가와 정답셋 채점 스크립트

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 12: README와 최종 점검

**Files:**
- Create: `README.md`
- Modify: `.superpowers/` 는 이미 gitignore

**Interfaces:** 없음

- [ ] **Step 1: README.md**

```markdown
# 음성 고객 응대 에이전트

브라우저에서 전화처럼 대화하는 쇼핑몰 상담 에이전트. 문의를 **분류**하고, 목 DB를 **조회**하고,
매뉴얼에 **근거**해 답하며, **가드레일**이 출처 없는 숫자를 막는다.

## 시작하기

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
cp .env.example .env            # OPENAI_API_KEY 채우기
.venv/bin/python -m server      # http://127.0.0.1:8000  (크롬 권장)
```

## 구조

| 경로 | 역할 |
|---|---|
| `server/router.py` | 분류 그래프 (classify → gate) |
| `server/context.py` | 매뉴얼 장 단위 분할, 라우트별 컨텍스트 |
| `server/tools.py` | 조회 도구 9개 |
| `server/answer.py` | 도구 호출 루프 |
| `server/guardrail.py` | 숫자 출처 역추적, 미확정값 확답 검사 |
| `server/pipeline.py` | 전체 그래프 + 통화별 체크포인터 |
| `server/app.py` | FastAPI |
| `web/` | 전화 화면 (voice.js 가 음성 모듈) |
| `domains/<이름>/` | 도메인 데이터 |
| `eval/` | 평가 스크립트 |

## 도메인 바꾸기

`domains/<새이름>/`에 `domain.json`, `policy.md`, `mockdb.json`, `eval/`을 같은 스키마로 만들고
`.env`에 `DOMAIN=<새이름>`. 라우트 이름 5개와 도구 9개, 목 DB 스키마는 고정이다.

## 평가

```bash
.venv/bin/python -m eval.eval_router --rule      # 규칙 기준선 (키 불필요)
.venv/bin/python -m eval.eval_router             # LLM 라우터 120건
.venv/bin/python -m eval.eval_answer             # 정답셋 첫 턴 34건
.venv/bin/pytest -q                              # 단위 테스트 (키 불필요)
```

한 군데 고치고 → 평가 → 숫자가 어디로 움직였나 본다. 평가셋(`split == "eval"`)은 절대 프롬프트에 넣지 않는다.

| # | 바꾼 것 | 라우팅 macro F1 | 답변 통과율 | 메모 |
|---|---|---|---|---|
| 0 | 기준선 (규칙 라우터) | | – | |
| 1 | LLM 라우터 | | | |

## 수동 검증 체크리스트

- [ ] "배송비 얼마예요?" → 상품을 되묻고 기본 배송비 2,500원만 안내하는가
- [ ] "청바지 반품되나요?" → 없는 상품을 있다고 하지 않는가
- [ ] "O-1006 반품 배송비 누가 내요?" → 검품 전이라 단정하지 않는가
- [ ] "홍대점 몇 시까지 해요?" → 범위 밖 안내 후 통화가 끊기는가
- [ ] "ㅂㅐ송비얼마임?" → 알아듣는가
- [ ] 1턴 "캔버스화 살 건데요" → 2턴 "배송비는요?" 가 앞 상품을 이어받는가

## 한계

- 상품명 연결이 토큰 겹침 + 오타 보정 수준이다. 실서비스는 임베딩 검색이 필요하다.
- 체크포인터는 메모리라 서버 재시작 시 통화가 사라진다.
- 음성은 크롬·엣지 계열의 Web Speech API에 의존한다.
```

- [ ] **Step 2: 전체 테스트와 정리**

Run: `.venv/bin/pytest -q`
Expected: 모두 통과

Run: `git status --short`
Expected: `.env`, `logs/`, `.venv/`가 목록에 없다.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: README - 시작하기, 도메인 교체, 평가, 수동 검증 체크리스트

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## 자체 검토

**스펙 대조**
- 3장 구조 → Task 1~11 파일 구조 일치. `eval/scoring.py`는 스펙에 없지만 채점 로직을 테스트 가능하게 분리한 것.
- 4.1 config → Task 2. 4.2 domain → Task 2. 4.3 router → Task 6. 4.4 context → Task 3. 4.5 tools → Task 4. 4.6 answer → Task 7. 4.7 guardrail → Task 5. 4.8 pipeline → Task 8. 4.9 app → Task 9.
- 5장 화면 → Task 10 (상태 머신, 상호 배타, 텍스트 대체, 음성 모듈 인터페이스, 패널).
- 6장 평가 → Task 11. 7장 테스트 → 각 Task의 테스트. 수동 체크리스트 → Task 12 README.
- 9장 성공 기준: 통화 한 바퀴(Task 10 Step 6), 체크리스트(README), 평가 스크립트(Task 11), DOMAIN 전환(Task 2 로더 + README), pytest 키 없이 통과(전 Task).

**타입 일관성**
- `Answerer.answer` 반환 `(str, dict, list[dict])` — Task 7 정의, Task 8 `FakeAnswerer`와 `_node_answer`, Task 11 `run()`이 같은 형태를 쓴다.
- `TurnResult` 필드 — Task 8 정의, Task 9 `FakePipeline`과 `to_dict()`, Task 10 `panel.addTurn`이 `route, confidence, action, tools, guardrail, elapsed_ms, answer, end_call`을 읽는다.
- `RouteDecision` — Task 6 정의, Task 8 테스트에서 같은 시그니처.
- `guardrail.check(answer, tool_results, domain)` — Task 5 정의, Task 8 `_node_guard`에서 같은 순서.
- 위반 유형 문자열 — Task 5 상수, Task 5 테스트 문자열 일치.
