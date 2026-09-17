# 진행 상태 정합성 구현 플랜 (4차)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 반품·교환이 진행 중인 주문에 배송을 물었을 때 "배송 완료되었습니다"로 끝나지 않고, 현재 진행 중인 사실을 먼저 알리게 만든다.

**Architecture:** 세 겹으로 막는다 — (1) 조회 도구가 "지금 진행 중인 것"을 분명한 필드로 주고, (2) 답변 프롬프트가 현재 상태와 과거 이벤트의 우선순위를 명시하고, (3) 가드레일이 누락을 잡는다. 더불어 (0) 주문과 반품의 단계가 어긋난 시드 데이터를 맞춘다.

**Tech Stack:** Python 3.12, SQLite, pytest. 측정은 `eval/` 스크립트(OpenAI 키 필요).

**Spec:** 별도 스펙 문서 없음 — 이 플랜 상단의 배경과 사용자 승인 사항(채팅)이 스펙 역할을 한다. 승인된 결정: ① 도구·프롬프트·가드레일 모두 수정 ② 데이터 모순도 함께 수정.

## 배경 (컨트롤러 실측, 2026-09-17)

사용자가 겪은 사례: 주문 `O-1072` 는 `status="반품진행"` 인데 배송 조회에 "9월 14일 배송 완료되었습니다" 로 답했다.

- `get_order_status("O-1072")` 는 `status="반품진행"`, `status_detail="접수"`, 그리고 `events` 마지막에 `{"at":"2026-09-14","status":"배송 완료"}` 를 함께 준다. 모델은 현재 상태를 무시하고 이벤트를 읽었다.
- 프롬프트의 절대 규칙 3번은 "조회 결과 없이 단정하지 않는다" 뿐이라, **status 와 events 가 어긋날 때 무엇이 사실인지 정하는 규칙이 없다.**
- 같은 함정에 걸리는 주문이 **49건**(반품진행·교환진행 주문 전부가 "배송 완료" 이벤트를 갖는다).
- 데이터 모순: 49건 중 **46건이 `status_detail="접수"`** 인데 실제 반품 단계는 검품중·수거완료·입고완료·환불완료다. `stage="환불완료"` 인 7건은 주문 상태가 아직 `반품진행` 이다.

## Global Constraints

- **측정은 한 번에 하나만 바꾸고 잰다.** 각 태스크는 독립 커밋이고, 5번 태스크에서 누적 측정한다. 악화되면 그 변경만 되돌리고 부정 결과로 기록한다(프로젝트 관행).
- `server/repo.py` 수정 금지. 도구 계약을 바꿀 때는 `tests/test_tools.py` 를 함께 고친다.
- 주석·문서·UI 문구는 한국어, 식별자는 영어.
- 테스트는 공유 DB(`domains/modumall/modumall.db`)에 쓰지 않는다(읽기만). 쓰기가 필요하면 인메모리 SQLite.
- 기존 테스트 347개가 계속 통과해야 한다: `.venv/bin/python -m pytest -q`
- `.venv` 는 uv venv (pip 없음, `uv pip install --python .venv/bin/python`). 새 의존성 금지.
- `.env` 의 키는 출력·커밋 금지.
- 커밋 메시지에 Co-Authored-By 트레일러를 붙이지 않는다.

---

### Task 1: 시드 데이터 정합성

**Files:**
- Modify: `server/db/generate.py`
- Test: `tests/test_generate.py`

**Interfaces:**
- Produces: `orders.status_detail` 이 그 주문의 `returns.stage` 에서 파생된다. 반품이 끝난 주문은 `orders.status` 가 종결 상태가 된다.

- [ ] **Step 1: 현재 상태 측정 기록**

```bash
.venv/bin/python -c "
import sqlite3; c=sqlite3.connect('domains/modumall/modumall.db')
print(c.execute(\"select o.status, o.status_detail, r.stage, count(*) from orders o join returns r on r.order_id=o.order_id where o.status in ('반품진행','교환진행') group by 1,2,3 order by 4 desc\").fetchall())
"
```
출력을 보고서에 붙인다. (컨트롤러 실측: 46건이 `접수`, 실제 stage 는 검품중 7·수거대기 6·수거완료 11·승인 8·입고완료 10·환불완료 7)

- [ ] **Step 2: 실패하는 테스트 작성** — `tests/test_generate.py` 에 추가

```python
def test_return_progress_detail_matches_return_stage(tmp_path):
    """반품·교환 진행 주문의 status_detail 은 그 반품의 현재 단계에서 파생돼야 한다."""
    from server.db.generate import generate

    db = generate(_domain_dir_copy(tmp_path))   # 기존 테스트가 쓰는 생성 헬퍼를 그대로 사용할 것
    con = sqlite3.connect(str(db))
    rows = con.execute(
        "select o.order_id, o.status, o.status_detail, r.stage from orders o "
        "join returns r on r.order_id = o.order_id "
        "where o.status in ('반품진행','교환진행')").fetchall()
    assert rows, "반품 진행 주문이 있어야 한다"
    for order_id, status, detail, stage in rows:
        assert stage in (detail or ""), f"{order_id}: 주문 상세 '{detail}' 가 반품 단계 '{stage}' 를 담지 않는다"


def test_finished_return_is_not_in_progress(tmp_path):
    """환불까지 끝난 반품의 주문이 '반품진행' 으로 남아 있으면 안 된다."""
    from server.db.generate import generate

    db = generate(_domain_dir_copy(tmp_path))
    con = sqlite3.connect(str(db))
    bad = con.execute(
        "select o.order_id from orders o join returns r on r.order_id = o.order_id "
        "where r.stage = '환불완료' and o.status in ('반품진행','교환진행')").fetchall()
    assert bad == [], f"환불완료인데 진행 중으로 남은 주문: {bad}"
```

`_domain_dir_copy` 는 기존 `tests/test_generate.py` 가 쓰는 방식(임시 디렉터리에 도메인 파일 복사 후 `generate`)을 그대로 따른다. **기존 파일을 먼저 읽고 그 헬퍼 이름·시그니처에 맞출 것.** 공유 DB 를 재생성하는 방식이면 안 된다.

- [ ] **Step 3: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_generate.py -q`
Expected: 두 테스트 FAIL (46건이 "접수", 환불완료 7건이 진행 중)

- [ ] **Step 4: 생성기 수정** — `server/db/generate.py`

반품/교환 주문을 만드는 자리(약 252행 `status_detail` 설정부)에서, 반품 레코드의 현재 `stage` 로부터 주문 상세를 파생시킨다. 매핑은 모듈 상단 상수로 둔다.

```python
# 반품·교환 단계 → 주문 화면에 보여줄 상세 문구. 주문과 반품이 다른 말을 하지 않도록 한 곳에서 파생한다.
RETURN_STAGE_DETAIL = {
    "접수": "반품 접수",
    "수거대기": "수거 대기",
    "수거완료": "수거 완료 · 입고 대기",
    "입고완료": "입고 완료 · 검품 대기",
    "검품중": "입고 완료 · 검품중",
    "승인": "승인 완료 · 환불 처리중",
    "환불완료": "환불 완료",
}
```

- 교환이면 문구의 "반품" 을 "교환" 으로 바꾼다(예: `"교환 접수"`).
- `stage == "환불완료"` 인 주문은 `status` 를 진행 상태로 두지 않는다. **어떤 종결 상태를 쓸지는 기존 status 값 집합(`결제완료·제작중·배송중·배송완료·반품진행·교환진행`)을 먼저 확인하고, 새 값을 만들지 말지 판단해 보고서에 근거를 쓴다.** 새 값을 추가하면 `server/adminrepo.py:IN_PROGRESS_STATUSES` 와 어드민 화면이 영향을 받으므로, 그 경우 해당 상수도 함께 고치고 관련 테스트를 확인한다.
- 손으로 만든 정식 주문 `O-1006`·`O-1007`·`O-1008` 은 이미 올바른 상세를 갖고 있다. 그 값들이 새 매핑과 충돌하면 매핑 쪽을 그 표현에 맞춘다(기존 정답셋이 이 문구를 기대할 수 있다).

- [ ] **Step 5: 통과 확인과 DB 재생성**

```bash
.venv/bin/python -m pytest tests/test_generate.py -q
.venv/bin/python -m server.db.generate --domain domains/modumall --force   # 실제 CLI 는 파일을 읽고 맞출 것
.venv/bin/python -m pytest -q
```
재생성 뒤 위 Step 1 쿼리를 다시 돌려 분포가 어떻게 바뀌었는지 보고서에 붙인다.

- [ ] **Step 6: 커밋**

```bash
git add server/db/generate.py tests/test_generate.py
git commit -m "fix: 반품·교환 주문의 상세 상태를 반품 단계에서 파생"
```

---

### Task 2: 조회 도구가 진행 중인 사실을 분명히 준다

**Files:**
- Modify: `server/tools.py` (`get_order_status`)
- Test: `tests/test_tools.py`

**Interfaces:**
- Consumes: `repo.order`, `repo.return_by_order`, `repo.shipment_events`
- Produces: `get_order_status(order_id)` 반환에 두 필드 추가
  - `active_process`: `None` 또는 `{"kind": "반품"|"교환", "return_id": str, "stage": str, "expected_completion": str|None}`
  - `events_note`: `"아래 events 는 지나간 배송 이력이며 현재 상태가 아니다"` (모델이 읽는 문장)

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_tools.py` 에 추가

```python
def test_order_status_exposes_active_return(tools):
    """반품이 진행 중인 주문은 그 사실을 별도 필드로 내려준다."""
    out = tools["get_order_status"]("O-1072")
    assert out["status"] == "반품진행"
    ap = out["active_process"]
    assert ap["kind"] == "반품" and ap["return_id"] == "R-2013"
    assert ap["stage"] == "수거완료"
    assert out["events_note"]                       # 이벤트가 과거 이력임을 알리는 문장이 있다


def test_order_status_has_no_active_process_when_delivered(tools):
    """배송만 끝난 평범한 주문에는 진행 중 프로세스가 없다."""
    delivered = <배송완료이고 반품이 없는 주문번호를 DB 에서 골라 쓸 것>
    out = tools["get_order_status"](delivered)
    assert out["status"] == "배송완료" and out["active_process"] is None
```

`tools` 픽스처는 기존 `tests/test_tools.py` 의 것을 그대로 쓴다(파일을 먼저 읽을 것). 두 번째 테스트의 주문번호는 **하드코딩하지 말고** DB 에서 `select order_id from orders o where o.status='배송완료' and not exists(select 1 from returns r where r.order_id=o.order_id) limit 1` 로 고른다.

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_tools.py -q`
Expected: FAIL — `KeyError: 'active_process'`

- [ ] **Step 3: 구현** — `server/tools.py` 의 `get_order_status`

```python
    def get_order_status(order_id: str) -> dict:
        """주문번호로 주문의 현재 진행 단계와 배송 정보를 조회한다."""
        o = repo.order(order_id)
        if not o:
            return {"error": "주문을 찾을 수 없습니다", "order_id": order_id}
        keys = ["order_id", "status", "status_detail", "is_external_channel", "items",
                "order_amount", "shipping_fee", "address_region", "courier", "tracking_no",
                "invoice_printed", "expected_ship_date"]
        out = {k: o[k] for k in keys if k in o}
        # 지금 진행 중인 반품·교환이 있으면 분명히 드러낸다. 배송 이벤트는 과거 이력이라
        # 그것만 읽으면 "배송 완료" 로 잘못 답하게 된다.
        r = repo.return_by_order(order_id)
        if r and r.get("stage") != "환불완료":
            out["active_process"] = {"kind": r.get("type") or "반품", "return_id": r["return_id"],
                                     "stage": r.get("stage"),
                                     "expected_completion": r.get("expected_completion")}
        else:
            out["active_process"] = None
        out["events"] = repo.shipment_events(order_id)
        out["events_note"] = "events 는 지나간 배송 이력이다. 현재 상태는 status 와 active_process 를 따른다."
        return clean(out)
```

주의: `clean()` 이 `None` 값을 떨어뜨리는 함수라면 `active_process: None` 이 사라질 수 있다. **`clean` 의 동작을 먼저 확인하고**, 없어진다면 테스트를 `out.get("active_process") is None` 으로 맞추거나 `clean` 뒤에 다시 넣는다. 어느 쪽을 택했는지 보고서에 쓸 것.

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_tools.py -q` 이어서 전체 `.venv/bin/python -m pytest -q`

- [ ] **Step 5: 커밋**

```bash
git add server/tools.py tests/test_tools.py
git commit -m "feat: 주문 조회에 진행 중인 반품·교환을 별도 필드로 노출"
```

---

### Task 3: 답변 규칙에 현재 상태 우선순위를 명시

**Files:**
- Modify: `server/prompts.py` (`build_answer_rules` 의 절대 규칙)
- Test: `tests/test_routes_consistency.py` 또는 프롬프트 문자열을 검사하는 기존 테스트 파일(먼저 확인)

**Interfaces:**
- Consumes: Task 2 의 `active_process`, `events_note`

- [ ] **Step 1: 규칙 추가** — `server/prompts.py`

절대 규칙 3번 아래에 다음 두 줄을 더한다(기존 번호 체계를 따라 번호를 부여할 것).

```
n. 주문의 현재 상태는 status 와 active_process 다. events 는 지나간 이력이므로, 이벤트에 '배송 완료'가
   있어도 status 가 반품진행·교환진행이면 현재 상태는 그쪽이다. 둘이 어긋나면 status 를 따른다.
n+1. active_process 가 있으면 무엇을 물었든(배송 조회 포함) 그 사실을 한 문장으로 먼저 알리고 나서
   물은 내용에 답한다. 예: "그 주문은 지금 반품 수거가 끝나 검품을 기다리는 중입니다. 배송은 9월 14일에
   완료됐었고요."
```

- [ ] **Step 2: 프롬프트 검사 테스트**

프롬프트 문자열을 검사하는 테스트가 이미 있으면 거기에, 없으면 `tests/test_prompts.py` 를 새로 만들어 다음을 검증한다.

```python
def test_answer_rules_state_priority():
    from pathlib import Path

    from server.domain import load_domain
    from server.prompts import build_answer_rules

    rules = build_answer_rules(load_domain(Path("domains/modumall")))
    assert "active_process" in rules
    assert "events" in rules and "지나간" in rules
```

이 테스트는 "규칙이 프롬프트에 들어 있다" 만 보장한다. **실제 효과는 Task 5 의 측정으로만 판정한다** — 이 테스트가 통과했다고 문제가 해결된 것은 아니라는 점을 보고서에 명시할 것.

- [ ] **Step 3: 전체 테스트와 커밋**

```bash
.venv/bin/python -m pytest -q
git add server/prompts.py tests/
git commit -m "feat: 답변 규칙에 현재 상태 우선순위와 진행 중 사실 우선 안내 추가"
```

---

### Task 4: 가드레일이 누락을 잡는다

**Files:**
- Modify: `server/guardrail.py`
- Test: `tests/test_guardrail.py`

**Interfaces:**
- Consumes: 도구 결과 dict(`tool_results`), 답변 문자열
- Produces: 새 위반 유형 상수 `VIOLATION_STALE_STATE = "진행 중 상태 누락"` 과 `check()` 안의 검사

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_guardrail.py` 에 추가

```python
def test_flags_answer_that_ignores_active_return(domain):
    """반품이 진행 중인데 '배송 완료' 만 말하고 그 사실을 빼먹으면 위반이다."""
    from server.guardrail import VIOLATION_STALE_STATE, check

    tool_results = {"get_order_status": {"order_id": "O-1072", "status": "반품진행",
                                         "active_process": {"kind": "반품", "return_id": "R-2013",
                                                            "stage": "수거완료"},
                                         "events": [{"at": "2026-09-14", "status": "배송 완료"}]}}
    bad = check("9월 14일에 수도권으로 배송 완료되었습니다.", tool_results, domain)
    assert not bad.ok and any(v["type"] == VIOLATION_STALE_STATE for v in bad.violations)

    good = check("그 주문은 지금 반품 수거가 끝나 검품을 기다리는 중입니다. 배송 자체는 9월 14일에 완료됐습니다.",
                 tool_results, domain)
    assert all(v["type"] != VIOLATION_STALE_STATE for v in good.violations)


def test_no_stale_state_flag_without_active_process(domain):
    """진행 중인 반품이 없으면 배송 완료라고 말해도 위반이 아니다."""
    from server.guardrail import VIOLATION_STALE_STATE, check

    tool_results = {"get_order_status": {"order_id": "O-1200", "status": "배송완료",
                                         "active_process": None,
                                         "events": [{"at": "2026-09-14", "status": "배송 완료"}]}}
    res = check("9월 14일에 배송 완료되었습니다.", tool_results, domain)
    assert all(v["type"] != VIOLATION_STALE_STATE for v in res.violations)
```

`domain` 픽스처는 기존 `tests/test_guardrail.py` 의 것을 쓴다(파일을 먼저 읽을 것).

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_guardrail.py -q`
Expected: FAIL — `ImportError: cannot import name 'VIOLATION_STALE_STATE'`

- [ ] **Step 3: 구현** — `server/guardrail.py`

```python
VIOLATION_STALE_STATE = "진행 중 상태 누락"

# 진행 중인 반품·교환을 언급했다고 볼 수 있는 말들. 하나라도 있으면 통과로 본다.
_PROCESS_WORDS = ("반품", "교환", "수거", "검품", "환불", "입고")
```

`check()` 안에서:

```python
    for result in tool_results.values():
        if not isinstance(result, dict):
            continue
        active = result.get("active_process")
        if not active:
            continue
        if not any(word in answer for word in _PROCESS_WORDS):
            violations.append({"type": VIOLATION_STALE_STATE,
                               "detail": f"{active.get('kind')} {active.get('stage')} 진행 중인데 답변이 그 사실을 말하지 않았다"})
        break
```

`tool_results` 의 실제 모양(도구 이름 → 결과 dict 인지, 리스트인지)을 **먼저 확인하고** 그 모양에 맞춰 순회할 것.

- [ ] **Step 4: 통과 확인과 커밋**

```bash
.venv/bin/python -m pytest -q
git add server/guardrail.py tests/test_guardrail.py
git commit -m "feat: 진행 중인 반품·교환을 빠뜨린 답변을 가드레일이 잡는다"
```

---

### Task 5: 측정

**Files:**
- Modify: `README.md` (실험 표에 한 행 추가), `docs/측정기록/` 에 원본 요약

**Interfaces:**
- Consumes: Task 1~4 의 변경 전부

- [ ] **Step 1: 재현 사례부터 확인 (키 필요 없음)**

`O-1072` 로 `get_order_status` 를 직접 호출해 `active_process` 가 채워지는지, `status_detail` 이 반품 단계와 맞는지 확인하고 출력을 보고서에 붙인다.

- [ ] **Step 2: 실제 통화로 재현**

서버를 **백그라운드로** 띄우고(`PORT=8010 .venv/bin/python -m server`), `/api/call/start` → `/api/call/turn` 을 curl 로 호출해 사용자가 겪은 흐름(그 주문 고객으로 전화 → "배송 조회 좀 부탁해")을 재현한다. 답변에 반품 진행 사실이 들어가는지 **원문 그대로** 보고서에 붙인다. 서버는 확인 후 종료한다. (LLM 키가 없어 실패하면 그 사실을 적고 다음 단계로 간다.)

- [ ] **Step 3: 평가 재측정**

```bash
.venv/bin/python -m eval.eval_answer        # 답변 정답셋 (judge 포함)
.venv/bin/python -m eval.eval_router        # 라우팅
.venv/bin/python -m eval.eval_regression    # 회귀
```
실제 실행 방법은 각 스크립트의 인자·환경변수를 먼저 확인할 것. **시간이 오래 걸리므로 반드시 백그라운드로 돌리고**, 중간에 끊기면 그 사실을 보고한다.

기준선(3차 최종): 답변 통과율 62.5%, 라우팅 F1 0.937, 회귀 6/6. 각 수치를 기준선과 비교해 표로 정리한다.

- [ ] **Step 4: 판정**

- 답변 통과율이 **떨어졌으면** 어느 태스크 때문인지 하나씩 되돌려 가며 재측정하고, 원인이 된 변경을 되돌린 뒤 **부정 결과로 기록**한다(프로젝트 관행).
- 올랐거나 같으면 그대로 둔다. 어느 쪽이든 숫자를 README 실험 표에 한 행으로 남긴다.

- [ ] **Step 5: 기록과 커밋**

`docs/측정기록/2026-09-17 진행상태 정합성 측정.md` 에 원본 요약을 쓰고, README 실험 표에 행을 추가한 뒤 커밋한다.

```bash
git add README.md docs/측정기록
git commit -m "docs: 진행 상태 정합성 측정 기록"
```

---

## 자기 점검 (플랜 작성자용)

- 배경의 4가지 사실(도구 출력·프롬프트 공백·49건 범위·데이터 모순) → Task 2·3 / Task 1 이 각각 대응.
- 승인 사항 ①(도구·프롬프트·가드레일) → Task 2·3·4. ②(데이터 모순) → Task 1.
- Task 1 이 상태 값을 새로 만들면 `server/adminrepo.py:IN_PROGRESS_STATUSES` 와 어드민 집계에 영향 — Step 4 에 경고를 넣어 두었다.
- Task 3 의 테스트는 "규칙이 프롬프트에 있다" 만 보장하므로 효과 판정은 Task 5 측정에 맡긴다고 명시했다.
- 측정에 키가 필요하고 오래 걸린다는 점, 악화 시 되돌리는 절차를 Task 5 에 적었다.
