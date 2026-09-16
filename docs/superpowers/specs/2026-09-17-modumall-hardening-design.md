# 3차: 모두몰 강화 설계

- 작성일: 2026-09-17
- 상태: 사용자 승인 (채팅에서 채점기 역할·확신도 방식·성공 기준 확정)
- 선행: `2026-09-16-eval-hardening-design.md`(2차), `2026-09-16-realistic-data-and-customer-id-design.md`(A단계). 둘 다 feat/voice-agent 에 구현 완료.
- 구현 방식: 계획은 Fable 이 쓰고, 코드 구현은 Opus 서브에이전트에 태스크 단위로 위임, Fable 이 검수한다.

## 1. 목표

2차 측정에서 남은 숫자를 움직인다. 답변 정답셋 통과율 50%(16/32)의 실패 원인은 되물어야 할 때 답변 3건, must 문자열 의미 불일치 6건, 도구 미호출 6건이다. 경계모호 15건은 확신도가 전부 0.5 이상이라 임계값이 한 번도 작동하지 않았다. 2차 최종 리뷰에서 보류한 "티" 동의어 과매칭과 동의어 값 검증도 이번에 처리한다.

| # | 항목 | 겨냥하는 숫자 | 효과 |
|---|---|---|---|
| 1 | 도구 호출 유도 프롬프트 | 도구 미호출 6건 | 라우트별 필수 도구 표와 자기 점검 규칙으로 조회 없이 답하는 경우를 줄인다 |
| 2 | LLM-as-judge 채점기 (규칙 실패 건만) | must 의미 불일치 6건 | "품절/불가/925원" 처럼 문자열은 다르지만 뜻이 맞는 답을 정답으로 인정한다 |
| 3 | 확신도 보정 임계값 + 후보 라우트 | 경계모호 위험 처리 15건 | 2순위 라우트와의 마진으로 애매함을 잡고, 임계값을 데이터로 정한다 |
| 4 | 히스토리 라우트 드리프트 보정 | (신규 측정) 멀티턴 라우트 정확도 | "배송비는요?" 같은 후속 발화가 앞 턴 라우트를 이어받게 한다 |
| 5 | `escalate_to_agent` → 통화 종료 | (버그) | 모델이 이관 도구를 불러도 무시되던 경로를 잇는다 |
| 6 | "티" 동의어 과매칭 | (2차 보류) | 조사·복합어 처리, 짧은 질의 오타 폴백 차단, 동의어 값 검증 |

## 2. 항목별 설계

### 2.1 도구 호출 유도 프롬프트

`server/prompts.py` `build_answer_rules` 에 두 가지를 더한다.

**라우트별 필수 도구 표.** 도메인의 라우트 정의(`domain.routes`)와 별개로 프롬프트에 고정 표를 넣는다. 표는 "이 조건이면 이 도구를 반드시 부른다" 형식이다.

| 상황 | 필수 도구 |
|---|---|
| SHIPPING 이고 상품이 특정됨 | `get_shipping_policy(product_id, order_amount)` |
| RETURN_REFUND 이고 상품이 특정됨 | `get_return_policy(product_id)` |
| RETURN_REFUND 이고 주문번호가 있음 | `get_order_status` 다음 `get_return_status` |
| PRODUCT_INFO 이고 구성·소재·재고·개별 구매를 물음 | `get_product_detail` |
| PRODUCT_INFO 이고 사이즈·색상·옵션을 물음 | `get_product_options` |
| 품절·재입고를 물음 | `get_restock_info` |

**자기 점검 규칙.** 4단계를 다음으로 바꾼다. "답변 초안에 금액·기간·재고·진행 상태가 들어가면, 그 값을 준 도구 이름을 스스로 확인한다. 매뉴얼 본문의 고정값(절대 규칙 1)이 아니고 도구 결과에도 없으면, 답하기 전에 그 도구를 부른다. 도구를 부를 수 있는데 부르지 않고 되묻거나 답하는 것은 실패다."

**측정 보조.** `eval/eval_answer.py` 출력 끝에 실패 사유 분포를 한 표로 집계한다. 사유 종류는 `action`, `tools 미호출`, `must 누락`, `forbid 위반`, `ASK 형식` 5가지이고 `score_turn` 의 실패 문자열 접두어로 분류한다. 한 케이스에 여러 사유가 있으면 각각 센다. 지금은 건별 문자열만 있어 어떤 사유가 줄었는지 손으로 세야 한다.

### 2.2 LLM-as-judge 채점기

새 파일 `eval/judge.py`.

- 대상: 규칙 채점(`score_turn`)의 실패 사유가 **`must 누락` 뿐인** 케이스. `action`, `tools 미호출`, `forbid 위반`, `ASK 형식` 중 하나라도 있으면 judge 를 부르지 않고 FAIL 이다. 이 사유들은 문자열 판정이 정확하고, judge 로 완화하면 회귀를 놓친다.
- 입력: 고객 질문, `expect.rubric`, `expect.reference`, 누락된 `must` 항목 목록, 실제 답변. 매뉴얼 본문은 주지 않는다. 판정 기준은 rubric 과 reference 가 이미 담고 있고, 매뉴얼을 주면 judge 가 스스로 답을 지어내 채점하는 문제가 생긴다.
- 출력: 구조화 출력 `JudgeVerdict(pass_: bool, reason: str)`. 프롬프트는 "누락된 must 항목 각각에 대해, 답변이 같은 사실을 다른 표현으로 전달했는지만 판단한다. reference 에 없는 사실을 답변이 추가로 말했다고 감점하지 않는다. must 항목 하나라도 사실이 다르거나 빠졌으면 실패다."
- 모델: `Settings.judge_model` (환경 변수 `JUDGE_MODEL`, 기본 `gpt-4.1-mini`). temperature 0.
- 함수: `judge_turn(llm, question, expect, answer, missing_must) -> JudgeVerdict`. llm 은 주입 가능해 테스트에서 가짜로 바꾼다.
- 자기 검증: `judge_self_check(llm, cases)` 는 각 케이스의 `reference` 를 답변으로 넣고 `must` 전부를 누락 항목으로 넘겨 judge 가 전부 pass 하는지 본다. 하나라도 fail 이면 judge 프롬프트가 틀린 것이다. `eval_answer --judge` 시작 시 한 번 돌리고 실패 conv_id 를 출력한다.

`eval/eval_answer.py` 변경:

- `--judge` 플래그(기본 꺼짐). 꺼지면 지금과 완전히 같다.
- 판정 열을 세 갈래로 늘린다. `PASS(규칙)`, `PASS(judge)`, `FAIL`. `--runs N` 이면 실행별 판정을 `aggregate_runs` 로 합치되, 규칙 통과와 judge 통과를 둘 다 "통과" 로 센다. 어느 쪽으로 통과했는지는 실행별 표시로 남긴다.
- 요약에 두 줄을 낸다. `규칙 통과율 a/32`, `judge 포함 통과율 b/32`. README 표에는 둘 다 기록한다. 이번 단계의 목표 70% 는 judge 포함 기준이다.
- judge 호출 수를 요약에 같이 낸다(비용 추적).

### 2.3 확신도 보정 임계값 + 후보 라우트

`server/router.py`:

- `RouteDecision` 에 `route_alt: Optional[Route]`(2순위 라우트, 없으면 None)와 `alt_confidence: float`(기본 0.0)를 추가한다. 프롬프트 [확신도 지침]을 "가장 가능성 높은 라우트를 `route` 에, 두 번째를 `route_alt` 에 쓰고 각각의 확신도를 낸다. 둘의 차이가 작을수록 애매한 문의다" 로 바꾼다. 기존 "0.5 미만으로 낮춰라" 문장은 유지한다.
- `node_gate`: `confidence < conf_threshold` **또는** `confidence - alt_confidence < conf_margin` 이면 ESCALATE. 그 외는 지금과 같다. `route_alt` 가 None 이면 마진은 1.0 으로 본다.
- `build_router(domain, conf_threshold, conf_margin, classify)` 시그니처에 `conf_margin` 추가. 규칙 라우터(`make_rule_classifier`)는 `route_alt=None` 을 내므로 기존 동작이 그대로다.
- `RouterState` 에 `route_alt`, `alt_confidence` 추가. `Pipeline` 상태와 `TurnResult` 에도 실어 화면 디버그 패널과 call_logs 에 남긴다.

`server/config.py`: `conf_margin: float` (환경 변수 `CONF_MARGIN`). **기본값은 측정 뒤에 정한다.** 코드 초기값은 0.0(비활성)으로 두고, 아래 격자 측정 결과로 `.env.example` 과 `config.py` 기본값을 한 번에 바꾼다.

`eval/eval_hard.py`:

- 라우터 호출은 지금처럼 72건 × 1회만 하고, 게이트 판정은 결과를 재사용해 오프라인으로 격자를 돈다. 임계값 `{0.5, 0.6, 0.7, 0.8}` × 마진 `{0.0, 0.1, 0.2, 0.3, 0.4}` 20칸에 대해 `자동 처리율`, `경계모호 위험 건수`, `비모호 케이스 중 잘못 이관된 건수` 세 값을 표로 낸다.
- 격자 표 아래에 "경계모호 위험 ≤ 5 를 만족하는 칸 중 자동 처리율 최대" 를 추천값으로 한 줄 출력한다. 사람이 이 값을 보고 기본값을 정한다.
- 보정표는 지금처럼 confidence 기준으로 유지하고, 마진 기준 보정표를 한 장 더 낸다(구간 0~0.1, 0.1~0.2, ..., 0.5 이상).

### 2.4 히스토리 라우트 드리프트 보정

`server/router.py`:

- `RouteDecision` 에 `is_followup: bool` 추가. 정의: "현재 발화가 직전 문의의 연속(같은 상품·같은 주문·같은 주제의 추가 질문)이면 true. 새 주제를 꺼내면 false." 라우터 입력에 직전 라우트가 없으면 항상 false.
- 라우터 입력 형식을 바꾼다. 지금은 `"{질문} (직전 발화: a / b)"` 한 문자열이다. 이를 `"{질문}\n[직전 문의] a / b\n[직전 라우트] SHIPPING"` 형식으로 하고, 프롬프트에 "[직전 라우트] 가 있고 현재 발화가 그 연속이면 is_followup=true 로 표시한다" 를 추가한다.

`server/pipeline.py`:

- `AgentState.routes: Annotated[list, operator.add]` 에 턴별 `{"route", "confidence", "is_followup"}` 을 쌓는다.
- `_node_route`: 직전 라우트 = `routes[-1]["route"]`(있을 때). 라우터 결과가 `is_followup=true` 이고 직전 라우트가 `OTHER` 가 아니면 `route` 를 직전 라우트로 덮어쓴다. 이때 게이트 판정은 라우터가 낸 action 을 그대로 쓴다(followup 은 라우트만 바꾸고 확신도 판정은 건드리지 않는다).
- 되묻기(ASK) 로 끝난 턴도 `routes` 에 기록한다. 되묻기 뒤 고객이 답하는 턴은 거의 항상 followup 이다.

측정용 데이터 `domains/modumall/eval/multiturn_routes.json` (신규, provenance=합성):

- 대화 20개, 각 2~3턴, 턴마다 `route` 정답. 최소 구성: 후속 발화가 짧아 단독으로는 다른 라우트로 보이는 대화 10개("레깅스 배송비요?" → "그럼 반품비는요?" 처럼 라우트가 바뀌는 것 4개 포함), 되묻기 뒤 답하는 대화 5개, 새 주제로 전환하는 대화 5개(followup 이 아니어야 함). 질문 표현은 `customer_inquiries.csv` 발화 패턴을 참고한다.
- `eval/eval_router.py --multiturn`: 대화마다 `_node_route` 와 같은 방식으로 직전 문의·직전 라우트를 넘기며 턴을 순서대로 라우팅한다. 출력: 턴 1 정확도, 턴 2 이후 정확도, followup 판정 혼동표(정답 followup 여부 × 예측). 비교 기준선은 같은 데이터를 히스토리 없이(단일 발화) 돌린 값이다.

### 2.5 `escalate_to_agent` → 통화 종료

`server/pipeline.py` `_node_answer`: `results` 키의 기본 이름(`#` 앞)이 `escalate_to_agent` 이고 값이 dict 이며 `escalated` 가 true 이면 `{"action": "ESCALATE", "tools": calls, "results": results, "attempts": attempts}` 를 반환한다. 이후 `_node_escalate` 가 `escalate_message` 를 답변으로 놓고 `turn()` 이 `end_call=True` 를 싣는 기존 경로를 탄다. `infer_action` 은 손대지 않는다(채점기와 공유하는 함수이고, 채점기는 이 도구를 기대 도구로 쓰지 않는다).

### 2.6 "티" 동의어 과매칭

`server/tools.py` `search_product`:

- 동의어 치환에 조사 제거를 적용한다. 토큰이 동의어 키와 같거나, 키 뒤에 `_particles` 의 조사만 붙은 형태("티는", "티만")면 치환한다. `_alias_match` 를 동의어 단계에서도 재사용한다.
- 오타 폴백(difflib·자모 유사도)은 `flat_q` 길이가 2 이하이면 건너뛴다. 한 글자·두 글자 질의는 우연한 유사도가 0.6 을 넘기 쉽고, 동의어·범주어에서 이미 처리되므로 폴백 이득이 없다.

`domains/modumall/domain.json` `search.synonyms` 에 복합어를 더한다: `"반팔티"`, `"긴팔티"`, `"무지티"` → `"티셔츠"`. 값은 카탈로그에 실제 있는 상품명 토큰이어야 한다.

`server/domain.py` `load_domain`: 동의어 값이 null 이 아니면 공백을 뺀 값이 mockdb 상품명(공백 제거) 중 하나에 부분 문자열로 들어 있어야 한다. 아니면 `DomainError("search.synonyms['{k}'] 값 '{v}' 이 어떤 상품명에도 없음")`. A단계 이후 실제 데이터는 SQLite 지만 상품명 집합은 mockdb.json 의 것과 동일하게 생성되므로 mockdb 로 검증해도 같다.

## 3. 파일 변경

| 파일 | 변경 |
|---|---|
| `server/prompts.py` | 필수 도구 표, 4단계 자기 점검, 라우터 확신도·followup 지침 |
| `server/router.py` | `route_alt`, `alt_confidence`, `is_followup`, 마진 게이트, `build_router` 시그니처 |
| `server/config.py` | `conf_margin`, `judge_model` |
| `server/pipeline.py` | `routes` 이력, followup 덮어쓰기, 라우터 입력 형식, `escalate_to_agent` 매핑, TurnResult 필드 |
| `server/tools.py` | 동의어 조사 제거, 짧은 질의 폴백 차단 |
| `server/domain.py` | 동의어 값 검증 |
| `server/app.py`, `web/` | 디버그 패널에 route_alt·마진·followup 표시(있으면) |
| `eval/judge.py` | 신규 |
| `eval/scoring.py` | 실패 사유 분류 함수 |
| `eval/eval_answer.py` | `--judge`, 세 갈래 판정, 사유 분포 |
| `eval/eval_hard.py` | 격자 표, 마진 보정표, 추천값 |
| `eval/eval_router.py` | `--multiturn` |
| `domains/modumall/domain.json` | 복합어 동의어 |
| `domains/modumall/eval/multiturn_routes.json` | 신규 |
| `.env.example`, `README.md` | 새 변수, 평가 명령, 실험표 4행 |
| `tests/` | 각 항목 단위 테스트 (키 없이) |

## 4. 측정 순서

같은 원칙을 지킨다. 한 군데 고치고, 평가하고, 숫자가 어디로 움직였나 본다. 판정기 변경과 에이전트 변경을 한 번에 재면 어느 쪽 효과인지 알 수 없다.

1. **judge 만 먼저** 붙이고 현재 코드(85b5543)로 `eval_answer --judge --runs 3` 을 돌린다. 이 값이 "판정기 효과" 다. 자기 검증 통과가 전제다.
2. 2.1 프롬프트 적용 후 다시 잰다. 도구 미호출 사유 건수 변화를 본다.
3. 2.3 적용 후 `eval_hard` 격자로 `CONF_THRESHOLD`·`CONF_MARGIN` 기본값을 정하고, `eval_router` 120건으로 F1 이 0.945 에서 떨어지지 않는지 확인한다. 떨어지면 마진을 낮춘다.
4. 2.4 적용 후 `eval_router --multiturn` 을 히스토리 없음 vs 있음으로 비교한다.
5. 2.5·2.6 은 단위 테스트로 확인하고, 마지막에 `eval_regression --runs 3` 과 `eval_answer --judge --runs 3` 을 최종으로 돌려 README 실험표 4행에 기록한다.

## 5. 범위에서 뺀 것

- 실시간(런타임) "도구를 불렀어야 했는데 안 불렀다" 모니터링 지표. 이번 단계는 평가 리포트로만 잰다.
- 전 건 judge 이중 채점, judge 로 규칙 대체. judge 는 규칙 실패의 must 누락 건만 본다.
- 임베딩 기반 상품 검색.
- 골든셋 자체의 수정. must 문자열이 과하게 좁다고 판단되는 건은 judge 가 흡수하고, 골든셋은 그대로 둔다.

## 6. 성공 기준

- `pytest` 키 없이 전부 통과(기존 180개 + 신규).
- 답변 통과율(judge 포함, runs=3) 70% 이상. 규칙 통과율도 함께 기록하며 50% 밑으로 떨어지지 않는다.
- `eval_hard` 경계모호 15건 중 확신 있게 처리(위험) 5건 이하. 격자 표에서 정한 기본값 기준.
- `eval_router` 120건 macro F1 0.945 유지(± 0.01). 회귀 스위트 6/6 PASS ×3.
- `eval_router --multiturn` 턴 2 이후 정확도가 히스토리 없음 기준선보다 높다.
- 가짜 도구로 `escalate_to_agent` 를 부르면 `TurnResult.end_call` 이 true 다.
- `search_product("티는")`·`search_product("반팔티")` 가 티셔츠로 확정되고, 두 글자 이하 오타 질의가 유사도 폴백으로 엉뚱한 상품에 붙지 않는다. 존재하지 않는 상품명을 동의어 값으로 넣으면 `DomainError` 다.
