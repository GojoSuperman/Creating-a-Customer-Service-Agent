# 실시간 턴 채점(LLM 분석) 구현 플랜

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 관리자 패널을 위아래로 나눠, 아래쪽에 방금 턴의 답변을 LLM 이 채점한 점수와 이유를 보여준다.

**Architecture:** 답변 생성과 **분리된 별도 요청**으로 채점한다(통화 지연 금지). 서버에 `server/turnscore.py`(채점 프롬프트·구조화 출력)와 `POST /api/call/score` 를 두고, 화면은 답변을 띄운 뒤 채점을 요청해 결과가 오면 하단 패널을 채운다.

**Tech Stack:** Python 3.12, FastAPI, LangChain `with_structured_output`, pydantic, 바닐라 JS, pytest

**Spec:** 별도 스펙 문서 없음 — 아래 승인 사항이 스펙이다.

## 사용자가 승인한 것

- **매 턴 LLM 채점**(통화 종료 후 일괄이 아니라 턴마다)
- 채점 기준 **4가지**: ①매뉴얼 준수 ②조회 근거 ③말투·간결성 ④고객 응대
- 관리자 패널을 **반으로 나눠** 하단에 표시

## 알아야 할 사실 (컨트롤러 확인)

- 기존 `eval/judge.py` 는 **정답셋 전용**이다(`expect.reference`·`must` 가 필요). 실시간에는 정답이 없으므로 **재사용할 수 없고 새 모듈이 필요**하다.
- 턴 로그(`server/pipeline.py` 약 397행)에는 이미 `q`·`a`·`route`·`confidence`·`action`·`tools`·`guardrail_ok` 가 쌓인다. 채점에 필요한 재료가 여기 있다.
- 방문자는 **자기 OpenAI 키**를 설정 모달에 넣어 쓴다(`X-OpenAI-Key` 헤더). 채점도 같은 키를 쓴다 — 키가 없으면 채점을 건너뛰고 안내만 표시한다.
- 채점 모델 기본값은 `settings.judge_model`(현재 `gpt-4.1-mini`)을 쓴다. 답변 모델(gpt-4.1)보다 싸다.
- 패널 구조: `web/index.html` 의 `<aside class="admin">` 안에 `<div id="panel">`(턴 카드, `web/panel.js` 가 렌더).

## Global Constraints

- **통화 흐름을 절대 막지 않는다.** 채점은 답변·음성 재생 뒤에 별도 요청으로, 실패해도 통화에 영향이 없어야 한다.
- 새 파이썬 의존성 금지(langchain·pydantic 은 이미 있음).
- 주석·UI 문구 한국어, 식별자 영어.
- `server/repo.py`·`server/adminrepo.py`·`server/shoprepo.py`·`server/admin.py`·`server/shop.py`·`server/pronounce.py` 수정 금지.
- 키는 로그·응답에 남기지 않는다(기존 `redact` 경로 유지).
- 기존 테스트 450개가 계속 통과: `.venv/bin/python -m pytest -q`
- 커밋 메시지에 Co-Authored-By 트레일러 금지. 푸시는 컨트롤러가 한다.

---

### Task 1: 채점 모듈

**Files:**
- Create: `server/turnscore.py`
- Test: `tests/test_turnscore.py`

**Interfaces:**
- Produces:
  - `class TurnScore(BaseModel)` — `manual: int`, `evidence: int`, `tone: int`, `service: int`(각 1~5), `reason: str`(한 문장), `total: int`(4항목 합, 4~20)
  - `build_score_messages(question, answer, tool_results, route, guardrail, manual_rules) -> list` — 채점 프롬프트
  - `make_scorer(model, api_key=None) -> Callable[[list], TurnScore]`
  - `score_turn(scorer, turn: dict) -> TurnScore`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_turnscore.py`

가짜 채점기(호출 가능 객체)를 주입해 프롬프트와 결과 처리를 검증한다. **실제 LLM 을 부르지 않는다.**

```python
def test_prompt_includes_answer_and_tool_results():
    msgs = build_score_messages(
        question="배송비 얼마예요?",
        answer="기본 배송비는 2,500원입니다.",
        tool_results={"get_shipping_policy": {"base_shipping_fee": 2500}},
        route="SHIPPING", guardrail={"ok": True, "violations": []},
        manual_rules="기본 배송비 2,500원")
    joined = " ".join(m["content"] if isinstance(m, dict) else str(m) for m in msgs)
    assert "2,500원" in joined and "get_shipping_policy" in joined
    assert "SHIPPING" in joined
    # 채점 기준 4가지가 프롬프트에 있어야 한다
    for k in ("매뉴얼", "조회", "말투", "응대"):
        assert k in joined


def test_score_turn_returns_structured_verdict():
    fake = lambda msgs: TurnScore(manual=5, evidence=4, tone=5, service=3, reason="근거는 충분하나 응대가 짧다")
    v = score_turn(fake, {"q": "배송비?", "a": "2,500원입니다.", "route": "SHIPPING",
                          "tools": ["get_shipping_policy"], "guardrail_ok": True})
    assert v.total == 17 and "응대" in v.reason


def test_total_is_derived_not_trusted_from_model():
    """모델이 total 을 엉뚱하게 줘도 4항목 합으로 다시 계산한다."""
    v = TurnScore(manual=1, evidence=1, tone=1, service=1, reason="x", total=999)
    assert v.total == 4


@pytest.mark.parametrize("bad", [0, 6, -1])
def test_scores_out_of_range_are_rejected(bad):
    with pytest.raises(Exception):
        TurnScore(manual=bad, evidence=3, tone=3, service=3, reason="x")
```

- [ ] **Step 2: 실패 확인 → Step 3: 구현**

채점 프롬프트는 짧고 분명하게. 핵심 지시:
- "너는 고객상담 품질 평가자다. 아래 답변을 네 기준으로 각각 1~5 점으로 매기고, 한 문장으로 이유를 써라."
- 기준 설명: **매뉴얼 준수**(정책에 없는 수치·조건을 지어내지 않았는가, 금지된 안내를 하지 않았는가) / **조회 근거**(도구를 불러야 할 때 불렀는가, 조회 결과와 다른 값을 말하지 않았는가) / **말투·간결성**(전화 상담답게 짧고 자연스러운가) / **고객 응대**(되물어야 할 때 되물었는가, 공감·사과 표현이 적절한가)
- 재료로 질문·답변·라우트·호출한 도구 목록·가드레일 결과·매뉴얼 요약을 준다.
- **모델이 답을 지어내 채점하지 않도록**, 정답을 모른다는 점과 "주어진 재료만으로 판단하라"를 명시한다.
- `total` 은 모델 출력을 믿지 말고 **서버에서 4항목 합으로 계산**한다(pydantic validator).

- [ ] **Step 4: 통과 확인 · Step 5: 커밋**

---

### Task 2: 채점 API

**Files:**
- Modify: `server/app.py`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: Task 1 의 `make_scorer`·`score_turn`, `pipeline.turn_logs`
- Produces: `POST /api/call/score` — 본문 `{"call_id": str, "turn": int}`(turn 은 0부터, 생략하면 마지막 턴). 응답 `{"manual","evidence","tone","service","total","reason"}`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_app.py` 에 추가

```python
def test_score_endpoint_scores_last_turn(client_with_fake_scorer):
    s = client.post("/api/call/start").json()
    client.post("/api/call/turn", json={"call_id": s["call_id"], "text": "배송비 얼마예요?"})
    r = client.post("/api/call/score", json={"call_id": s["call_id"]})
    assert r.status_code == 200
    body = r.json()
    assert 4 <= body["total"] <= 20 and body["reason"]


def test_score_endpoint_404_for_unknown_call(client):
    assert client.post("/api/call/score", json={"call_id": "없는통화"}).status_code == 404


def test_score_endpoint_needs_key(client_without_env_key):
    """키가 없으면 401 로 안내한다 (통화 자체는 계속된다)."""
    ...
```

가짜 채점기를 주입할 수 있게 `create_app` 또는 모듈 수준에서 교체 가능한 구조로 만들 것(기존 `tts` 주입 방식을 참고).

- [ ] **Step 2: 실패 확인 → Step 3: 구현**

- 키 결정 순서는 기존과 동일: `X-OpenAI-Key` 헤더 → 서버 환경변수 → 둘 다 없으면 **401**(화면이 안내).
- 채점 실패(LLM 오류·타임아웃)는 **500 이 아니라 그 사실을 담은 응답**으로 돌려 화면이 "채점 실패"를 표시하게 한다.
- 채점 결과를 서버에 저장하지 않는다(이번 범위 밖). 같은 턴을 두 번 요청하면 두 번 채점한다 — 화면이 중복 요청하지 않게 만든다.

- [ ] **Step 4: 통과 확인 · Step 5: 커밋**

---

### Task 3: 패널 분할과 점수 표시

**Files:**
- Modify: `web/index.html`, `web/style.css`, `web/call.js`, `web/panel.js`
- Create: `web/scorepanel.js`

- [ ] **Step 1: 구현**

- `<aside class="admin">` 안을 위아래로 나눈다: 위 `#panel`(기존 턴 카드, 스크롤), 아래 `#score-panel`(신규). 비율은 **위 60% / 아래 40%** 정도로 시작하되, 아래가 비어 있을 때는 안내 한 줄만 보이게.
- 답변을 받아 말풍선을 그린 **뒤에** `/api/call/score` 를 호출한다. 응답 전에는 "채점 중…" 을 보여준다.
- 표시 내용: 4개 기준의 점수(막대 또는 5칸 점), 총점(예: `17 / 20`), 한 문장 이유, 그리고 그 턴의 라우트·확신도(이미 있는 값)를 함께.
- 401(키 없음)이면 "설정에서 OpenAI 키를 넣으면 채점이 표시됩니다" 안내.
- 채점 실패는 조용히 "채점 실패" 로 표시하고 통화는 계속.
- **중복 호출 금지**: 같은 턴에 대해 한 번만 요청한다.

- [ ] **Step 2: 확인**

서버를 8010 이 아닌 포트에 띄워 `/api/call/score` 응답을 curl 로 확인하고, 화면 동작은 사용자 확인 항목으로 남긴다.

- [ ] **Step 3: 커밋**

---

### Task 4: 검증

- [ ] 전체 테스트 통과
- [ ] 실제 통화 2~3턴을 돌려 점수가 뜨는지, 통화가 느려지지 않는지 확인(답변 도착 시각과 점수 도착 시각을 각각 기록)
- [ ] 키 없이 통화했을 때 안내가 뜨고 통화는 정상인지
- [ ] README 에 기능 한 줄 추가

## 자기 점검 (플랜 작성자용)

- 승인 사항 3가지 → 매 턴 채점(Task 1·2), 기준 4개(Task 1 프롬프트), 패널 분할(Task 3).
- "통화를 막지 않는다" → Task 2 가 별도 엔드포인트, Task 3 이 답변 뒤 비동기 호출.
- BYOK·키 보호 → Task 2 가 기존 헤더 경로 재사용.
- 기존 `eval/judge.py` 를 재사용할 수 없는 이유를 플랜 본문에 명시했다.
