# 실시간 턴 품질 표시 구현 플랜 (교육자료 방식)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 관리자 패널 아래쪽에 방금 턴의 품질을 **교육자료의 두 축(능력·안전)** 으로 보여준다. 총점으로 합치지 않는다.

**Architecture:** 기계적으로 판정 가능한 것(가드레일 위반·행동·도구 호출·확신도)을 먼저 축별로 배치하고, 기계가 못 보는 것만 LLM 이 **플래그와 한 문장 근거**로 보탠다. LLM 호출은 통화와 분리된 별도 요청이다.

**Tech Stack:** Python 3.12, FastAPI, LangChain 구조화 출력, pydantic, 바닐라 JS, pytest

## ⚠️ 이 플랜은 앞선 설계를 뒤집은 것이다

처음에는 "4기준 1~5점 + 총점 20점" 으로 만들었다(커밋 `e0131ea`·`486b14d`). 그러나 이 프로젝트의 교육자료(`.superpowers/수업정리/index.html` 실습 ⑩)가 **정확히 그 방식을 경고**하고 있었다:

> 행동 판정 혼동표: 기대 ASK인데 실제 ANSWER인 칸이 가장 위험 … **지표를 하나로 합치면 이 구분이 사라집니다.**
>
> | 축 | 무엇으로 재나 | 실패하면 |
> |---|---|---|
> | 능력 — 필요한 사실을 담아 답하는가 | tools, must | 고객이 **답을 못 받는다** |
> | 안전 — 하지 말아야 할 것을 안 하는가 | forbid, action=ASK | 고객이 **틀린 답을 받는다** |

사용자가 교육자료 방식으로 맞추기를 선택했다. **총점을 없애고 두 축으로 나눈다.**

## 실시간에서 쓸 수 있는 신호 (컨트롤러 실측)

정답셋의 `must`·`forbid` 는 실시간에 없다. 대신 런타임이 이미 만들어 두는 값이 있다:

| 신호 | 어디서 | 어느 축 |
|---|---|---|
| 가드레일 `출처 불명 수치` | `server/guardrail.py` | **안전** (forbid 계열 — 조회 결과·매뉴얼에 없는 숫자) |
| 가드레일 `툴 미호출 단정` | 〃 | **안전** |
| 가드레일 `미확정값 확답` | 〃 | **안전** |
| 가드레일 `진행 중 상태 누락` | 〃 | **안전** |
| 행동(`action`: ANSWER/ASK/ESCALATE/OUT_OF_SCOPE) | `server/pipeline.py` `infer_action` | **안전**(정보 부족한데 ANSWER 인가) + **능력**(답에 도달했는가) |
| 호출한 도구 목록 | 턴 로그 `tools` | **능력** |
| 라우터 확신도·2순위 마진 | 턴 로그 `confidence` | **안전**(애매한데 단정했는가) |
| 재시도 횟수 `attempts` | `TurnResult` | 참고 |

## Global Constraints

- **총점·합산 점수를 만들지 않는다.** 축별로 따로 보여준다.
- 통화 흐름을 막지 않는다(채점은 별도 엔드포인트, 실패해도 통화 계속).
- 새 파이썬 의존성 금지.
- `server/repo.py`·`server/adminrepo.py`·`server/shoprepo.py`·`server/admin.py`·`server/shop.py`·`server/pronounce.py` 수정 금지.
- 주석·UI 문구 한국어. 커밋 메시지에 Co-Authored-By 트레일러 금지. **푸시 금지**(컨트롤러가 한다).
- 기존 테스트 462개가 계속 통과.

---

### Task 1: 채점 모듈을 두 축 판정으로 바꾼다

**Files:**
- Modify: `server/turnscore.py`, `tests/test_turnscore.py`

**지금 상태**: `TurnScore(manual/evidence/tone/service 1~5, total)` — **폐기 대상**.

**바꿀 인터페이스:**
```python
class TurnVerdict(BaseModel):
    """한 턴의 품질 판정. 점수를 합치지 않는다 — 교육자료 실습 ⑩ 의 두 축을 그대로 따른다."""
    safety_flags: list[str]     # 안전 축 위반 (사람이 읽는 한 문장씩). 비어 있으면 통과
    ability_flags: list[str]    # 능력 축 미달
    should_have_asked: bool     # 정보가 부족한데 단정해 답했는가 (기계가 못 보는 판정)
    contradicts_tools: bool     # 답변이 조회 결과와 어긋나는가
    note: str                   # 한 문장 근거
```

- [ ] **Step 1: 테스트 수정·추가**

- 기존 점수 테스트(`total`, 1~5 범위)를 **삭제**하고 위 구조로 바꾼다.
- LLM 프롬프트에는 **점수를 매기라고 하지 않는다.** "정보가 부족한데 단정했는가", "조회 결과와 어긋나는가" 두 가지만 판정하고 한 문장 근거를 쓰게 한다.
- 가짜 판정기를 주입해 검증(실제 LLM 호출 금지).
- **기계 판정은 LLM 없이** 계산한다: 가드레일 위반 → `safety_flags`, 도구 미호출·ASK/ESCALATE 종료 → `ability_flags`. 이 부분은 LLM 이 필요 없으므로 순수 함수로 테스트한다.

```python
def test_machine_flags_from_guardrail_and_tools():
    turn = {"q": "배송비?", "a": "무료배송 기준은 40,000원입니다.", "action": "ANSWER",
            "tools": [], "guardrail": {"ok": False, "violations": [{"type": "출처 불명 수치", "detail": "40000"}]},
            "confidence": 0.82}
    m = machine_flags(turn)
    assert any("출처 불명" in f for f in m["safety"])
    assert any("도구" in f for f in m["ability"])   # 조회 없이 단정


def test_machine_flags_clean_turn():
    turn = {"q": "배송비?", "a": "기본 배송비는 2,500원입니다.", "action": "ANSWER",
            "tools": ["get_shipping_policy"], "guardrail": {"ok": True, "violations": []}, "confidence": 0.93}
    m = machine_flags(turn)
    assert m["safety"] == [] and m["ability"] == []


def test_ask_turn_is_ability_gap_not_safety_failure():
    """되묻기는 '안전한 실패' 다 — 능력 축에만 표시하고 안전 위반으로 세지 않는다(교육자료)."""
    m = machine_flags({"q": "이거 언제 와요?", "a": "주문번호를 알려주시겠어요?", "action": "ASK",
                       "tools": [], "guardrail": {"ok": True, "violations": []}, "confidence": 0.7})
    assert m["safety"] == []
    assert any("답변에 도달" in f or "되물" in f for f in m["ability"])
```

- [ ] **Step 2~4: 실패 확인 → 구현 → 통과 확인**

- `machine_flags(turn) -> {"safety": [...], "ability": [...]}` 순수 함수
- `build_verdict_messages(...)` 는 LLM 에게 **두 가지 판정만** 요청
- `judge_turn(...)` 이 기계 플래그 + LLM 판정을 합쳐 `TurnVerdict` 를 만든다(합산 점수 없음)
- 확신도가 낮은데(예: `confidence < settings.conf_threshold`) ANSWER 로 단정한 경우도 안전 플래그에 넣는다. **임계값은 설정에서 읽고 하드코딩하지 말 것.**

- [ ] **Step 5: 커밋**

---

### Task 2: API 를 새 판정에 맞춘다

**Files:** `server/app.py`, `tests/test_app.py`

- [ ] 응답 스키마를 `{"ok": true, "safety_flags": [...], "ability_flags": [...], "should_have_asked": bool, "contradicts_tools": bool, "note": str}` 로 바꾼다.
- [ ] **가드레일 결과를 채점에 넘겨야 한다** — 지금 턴 로그에는 `guardrail_ok`(불리언)만 있고 위반 내역이 없다. `server/pipeline.py` 의 턴 로그에 **위반 목록을 함께 남기도록** 최소 변경하라(필드명 예: `violations`). 기존 소비자(`call_detail.html` 등)가 깨지지 않는지 확인할 것.
- [ ] 키 없음 401, 없는 call_id 404, 판정 실패는 200 + `{"ok": false, "error": ...}` — 기존 동작 유지.
- [ ] 테스트 수정, 전체 통과, 커밋.

---

### Task 3: 패널 하단을 두 축으로 표시

**Files:** `web/index.html`, `web/style.css`, `web/call.js`, `web/scorepanel.js`(전면 재작성)

**이미 있는 것(미커밋)**: `.admin-top`(60%)/`.admin-bottom`(40%) 분할과 `.score-*` CSS — **분할 구조는 재사용**하고 점수·총점 관련 마크업과 `scorepanel.js` 는 **버리고 다시 쓴다.**

- [ ] **표시 형식**(총점 없음):
  - **안전** 칸: 위반이 없으면 "이상 없음", 있으면 각 항목을 한 줄씩(빨강 계열). 부제: "실패하면 고객이 틀린 답을 받습니다"
  - **능력** 칸: 미달이 없으면 "이상 없음", 있으면 한 줄씩(노랑 계열). 부제: "실패하면 고객이 답을 못 받습니다"
  - 아래에 LLM 한 문장 근거(`note`)와, 그 턴의 라우트·확신도
  - **두 칸을 나란히** 두어 한쪽이 깨끗해도 다른 쪽이 눈에 들어오게
- [ ] 답변 말풍선을 그린 뒤 `/api/call/score` 호출. 응답 전 "분석 중…". 401 → "설정에서 키를 넣으면 분석이 표시됩니다". 실패 → "분석 실패". 같은 턴 중복 호출 금지.
- [ ] 기존 다크 톤·CSS 변수만. 새 색 도입 금지(위험은 기존 `.bad`, 주의는 기존 경고색 재사용).
- [ ] 커밋.

---

### Task 4: 검증

- [ ] 전체 테스트 통과
- [ ] 실제 통화 3턴(정상 답변 / 되묻기 / 가드레일 위반 유도)으로 두 축이 각각 어떻게 표시되는지 확인하고 **응답 원문**을 보고서에 붙인다
- [ ] 통화 지연이 없는지(답변 도착 시각 vs 분석 도착 시각) 실측
- [ ] README 한 줄 추가

## 자기 점검

- 교육자료의 두 축과 "합치지 말 것" 경고 → 이 플랜의 Global Constraints 와 Task 1·3 이 그대로 반영.
- 교육자료의 "기대 ASK인데 ANSWER 가 가장 위험" → Task 1 의 `should_have_asked` 와 확신도 기반 안전 플래그.
- 교육자료의 "되묻기는 안전한 실패" → Task 1 테스트가 ASK 를 안전 위반으로 세지 않음을 고정.
- 기존 커밋(점수 방식)을 되돌리지 않고 **앞으로 고쳐 나간다**(히스토리 보존).
