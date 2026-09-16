# 음성 고객 응대 에이전트 설계

- 작성일: 2026-09-16
- 상태: 검토 대기
- 참고: 모두의연구소 「고객 응대 에이전트 만들기 — 라우팅과 그라운딩」 수업 자료 및 실습 코드 (참고만 하고 코드는 새로 작성)

## 1. 목표

브라우저에서 전화가 걸려온 것처럼 대화하는 고객 응대 에이전트를 만든다. 고객이 마이크로 말하면 인식하고, 에이전트가 답변을 TTS로 읽어 준다. 에이전트는 수업의 핵심 구조를 따른다.

1. 문의를 5개 라우트 중 하나로 **분류**하고, 확신이 없으면 사람에게 넘긴다.
2. 건마다 달라지는 값은 LLM이 말하지 않고 **조회 도구**가 가져온다.
3. 매뉴얼과 조회 결과에만 **근거**해서 답한다.
4. 답변 속 숫자의 출처를 **가드레일**이 역추적해 출처 없는 숫자를 막는다.
5. 정답셋으로 **채점**해 무엇이 부족한지 숫자로 말한다.

도메인은 모두몰을 기본으로 하되, 데이터 폴더만 바꾸면 다른 쇼핑몰로 전환할 수 있어야 한다.

## 2. 확정된 요구사항

| 항목 | 결정 |
|---|---|
| 결과물 형태 | 웹 앱. 전화 연출 + 음성 입력/음성 답변 + 텍스트 입력 대체 경로 + 관리자 패널 |
| LLM | OpenAI. 라우팅용과 답변용 모델을 따로 지정 |
| 스택 | Python 백엔드(FastAPI + LangGraph + langchain-openai), 브라우저 화면(단일 HTML + JS) |
| 음성 | 브라우저 Web Speech API (SpeechRecognition, speechSynthesis). 교체 가능한 모듈로 분리 |
| 도메인 | `domains/<이름>/` 폴더 단위. 기본 `modumall` |
| 평가 | 라우팅 평가(macro F1)와 정답셋 채점 스크립트 포함 |
| 턴 전송 | HTTP POST 한 번. 스트리밍 없음 |

## 3. 전체 구조

```
customer-service-agent/
├── server/
│   ├── app.py          # FastAPI. 정적 화면 서빙 + API 3개
│   ├── config.py       # 모델명, 임계값, 활성 도메인. 환경 변수로 덮어쓰기
│   ├── domain.py       # 도메인 폴더 로더와 검증
│   ├── router.py       # 분류 그래프: classify(LLM) → gate(정책)
│   ├── context.py      # 매뉴얼 장 단위 분할, 라우트별 컨텍스트 조립
│   ├── tools.py        # 조회 도구 9개 (목 DB 기반)
│   ├── answer.py       # agent ↔ tools 도구 호출 루프
│   ├── guardrail.py    # 숫자 출처 역추적 + 미확정값 확답 검사
│   ├── pipeline.py     # 전체 그래프 + 체크포인터
│   └── prompts.py      # 라우팅 지침·답변 규칙 뼈대
├── web/
│   ├── index.html
│   ├── call.js         # 전화 상태 머신
│   ├── voice.js        # 음성 인식·TTS 모듈
│   └── panel.js        # 관리자 패널
├── domains/
│   └── modumall/
│       ├── domain.json
│       ├── policy.md
│       ├── mockdb.json
│       └── eval/       # customer_inquiries.csv, routing_answers.csv, hard_cases.csv, answer_goldenset.json
├── eval/
│   ├── eval_router.py
│   └── eval_answer.py
├── tests/
├── logs/               # gitignore
├── requirements.txt
├── .env.example
└── README.md
```

### 한 턴의 흐름

1. 화면이 인식된 문장을 `POST /api/call/turn {call_id, text}`로 보낸다.
2. 서버가 체크포인터에서 그 통화의 상태를 복원하고 그래프를 한 바퀴 돌린다.
3. `{answer, route, confidence, action, tools, guardrail, elapsed_ms}`를 돌려준다.
4. 화면이 `answer`를 TTS로 읽고, 나머지를 관리자 패널에 표시한다.

## 4. 서버 컴포넌트

### 4.1 config.py

| 설정 | 기본값 | 환경 변수 |
|---|---|---|
| 라우팅 모델 | `gpt-4.1-mini` | `ROUTER_MODEL` |
| 답변 모델 | `gpt-4.1` | `ANSWER_MODEL` |
| 확신도 임계값 | 0.5 | `CONF_THRESHOLD` |
| 도구 호출 루프 상한 | 3 | `MAX_TOOL_TURNS` |
| 가드레일 재생성 횟수 | 1 | `GUARDRAIL_RETRY` |
| 활성 도메인 | `modumall` | `DOMAIN` |

모델 기본값은 구현 시점에 OpenAI에서 실제로 호출 가능한 이름으로 확인해 확정한다. 수업 자료의 모델명(`gpt-5.6-luna` 등)은 쓰지 않는다.

### 4.2 domain.py

`domains/<DOMAIN>/`을 읽어 `Domain` 객체 하나를 만든다. 시작 시 필수 파일과 `domain.json` 필수 키를 검증하고, 빠진 것이 있으면 어떤 파일·키가 없는지 명시한 오류로 종료한다.

`domain.json` 구조:

```json
{
  "name": "모두몰",
  "greeting": "모두몰 고객센터입니다. 무엇을 도와드릴까요?",
  "out_of_scope_message": "해당 내용은 ... 문의해 주셔야 확인이 가능합니다.",
  "escalate_message": "정확한 확인을 위해 상담원에게 연결해 드리겠습니다.",
  "routes": {
    "ORDER_PLACE":   {"label": "주문·구매", "definition": "구매·주문 접수, 구매 가능 여부, ...", "sections": ["2"]},
    "PRODUCT_INFO":  {"label": "상품 문의", "definition": "...", "sections": ["3"]},
    "SHIPPING":      {"label": "배송",     "definition": "...", "sections": ["4"]},
    "RETURN_REFUND": {"label": "교환·반품·환불", "definition": "...", "sections": ["5", "6"]},
    "OTHER":         {"label": "범위 밖",  "definition": "...", "sections": []}
  },
  "always_sections": ["이 매뉴얼을 쓰는 방법", "0", "1", "7", "8"],
  "routing_rules": "고객이 쓴 단어가 아니라 ... (매뉴얼 1.1·1.2·7.1 발췌)",
  "fixed_values": {"base_shipping_fee": 2500, "return_fee_full": 5000, "...": "..."},
  "small_numbers_allowed": [1, 2, 3, 4, 7, 11]
}
```

라우트 이름 5개(`ORDER_PLACE`, `PRODUCT_INFO`, `SHIPPING`, `RETURN_REFUND`, `OTHER`)와 도구 9개의 이름·시그니처, 목 DB 스키마는 **코드에 고정**한다. 도메인이 바꿀 수 있는 것은 정의문, 매뉴얼 장 매핑, 고정값, 안내 문구, 데이터 내용이다. 도구 자체를 바꿔야 하는 도메인은 이번 범위에서 제외한다.

### 4.3 router.py

LangGraph `StateGraph` 노드 두 개.

- `classify`: LLM에 구조화 출력(`RouteDecision{route: Literal[5종], confidence: 0~1, reason: str}`)을 강제한다. 시스템 프롬프트는 `prompts.ROUTE_GUIDE` 뼈대에 도메인의 라우트 정의와 `routing_rules`를 채워 만든다.
- `gate`: `confidence < 임계값`이면 `ESCALATE`, `route == OTHER`면 `OUT_OF_SCOPE`, 그 외 `HANDLE`.

`classify`는 함수 주입이 가능해야 한다(테스트에서 가짜 분류기, 평가에서 규칙 분류기 비교).

### 4.4 context.py

- `split_sections(text)`: `## ` 헤딩 단위로 분할. 키는 장 번호 문자열, 번호 없는 장은 제목 그대로. `부록`으로 시작하는 장은 제외.
- `build_context(route)`: `always_sections + routes[route].sections` 순서로 이어 붙인다.
- 검증 규칙: SHIPPING 컨텍스트에는 5·6장 내용이 없어야 한다(테스트로 고정).

### 4.5 tools.py

목 DB를 읽어 도구 9개를 만든다. 이름과 시그니처는 고정.

| 도구 | 입력 | 출력 요지 |
|---|---|---|
| `search_product(query)` | 상품명 일부 | 후보 목록, `resolved_product_id`(후보가 하나일 때), `ambiguous` |
| `get_order_status(order_id)` | 주문번호 | 진행 단계, 외부 채널 여부, 금액, 지역 |
| `get_product_detail(product_id)` | 상품 ID | 구성·소재·재고·보증서 |
| `get_product_options(product_id)` | 상품 ID | 개별 구매 가능 여부, 옵션 |
| `get_shipping_policy(product_id, order_amount: Optional[int])` | 상품 ID, 금액 | 무료배송 기준, 배송비, **부족액(도구가 계산)** |
| `get_return_policy(product_id)` | 상품 ID | 카테고리별 반품 기간·조건 |
| `get_return_status(order_id)` | 주문번호 | 수거·검품·환불 단계. 미확정은 `null` 그대로 |
| `get_restock_info(product_id)` | 상품 ID | 확정 여부, 예정일 |
| `escalate_to_agent(reason, context)` | 사유 | 이관 접수 결과 |

원칙: 없는 ID는 `{"error": ...}`를 돌려준다. `$`로 시작하는 키는 재귀적으로 제거한다. 선택 인자는 반드시 `Optional[...]`로 적는다. docstring 첫 줄이 모델이 읽는 설명이다.

`search_product`는 토큰 겹침 점수에 더해 공백 제거 부분 일치와 `difflib` 유사도를 함께 써서 "캔버스화", "원피스"처럼 범주 이름으로 부르는 경우의 발견율을 높인다. 그래도 후보가 여럿이면 `ambiguous`로 돌려주고 되묻게 한다.

### 4.6 answer.py

노드 둘(`agent`, `tools`)과 조건부 엣지 하나로 된 도구 호출 루프. `recursion_limit = 2 * MAX_TOOL_TURNS + 1`. 상한에 걸리면 이관 문구와 빈 결과를 돌려준다. 그 밖의 예외는 잡지 않는다.

시스템 프롬프트는 `prompts.ANSWER_RULES`(절차 4단계 + 절대 규칙 6개) + 라우트별 컨텍스트 + 대화 이력. 반환값은 `(answer_text, {tool_name: result})`.

### 4.7 guardrail.py

허용 집합 = 조회 결과 속 숫자 + 도메인 고정값 + 한 단계 산술 유도값. 답변 속 1,000 이상 숫자 중 허용 집합에 없는 것이 있으면 위반.

추가 검사 두 가지.

1. **툴 미호출 단정**: "무료배송" + "N원 이상/부터" 패턴이 있는데 `get_shipping_policy`를 부르지 않았으면 위반.
2. **미확정값 확답**: 조회 결과에 `is_confirmed: false` 또는 `null` 필드가 있는데 답변에 "예정일은 …입니다", "…이(가) 부담하셔야", "…로 확정" 같은 확답 패턴이 있으면 위반.

위반은 `logs/guardrail.jsonl`에 `{ts, call_id, route, type, detail, answer}`로 기록한다.

### 4.8 pipeline.py

```
START → route → [HANDLE] → answer → [ASK] → END
                          │          [OUT_OF_SCOPE] → escalate → END
                          │          [else] → guard → [ok] → END
                          │                          [violation, attempts<2] → answer
                          │                          [violation, attempts>=2] → escalate → END
                          └─[ESCALATE / OUT_OF_SCOPE] → escalate → END
```

`AgentState`: `question, history(누적), route, confidence, action, tools, results, answer, guardrail, attempts`. 체크포인터는 `InMemorySaver`, `thread_id = call_id`. `history`는 앞 턴의 고객 발화 목록이며 라우팅과 답변 모두에 앞 턴 문장을 이어 붙여 넘긴다.

`action` 판정: 라우터가 넘긴 값에 더해, 답변 노드에서 도구를 하나도 부르지 않고 물음표로 끝나면 `ASK`, 주문 조회 결과가 외부 채널이면 `OUT_OF_SCOPE`, 가드레일 통과면 `ANSWER`.

### 4.9 app.py

| 메서드 | 경로 | 요청 | 응답 |
|---|---|---|---|
| GET | `/` | – | `web/index.html` |
| GET | `/api/domain` | – | `{name, greeting}` |
| POST | `/api/call/start` | – | `{call_id, greeting}` |
| POST | `/api/call/turn` | `{call_id, text}` | `{answer, route, confidence, action, tools: [{name, args}], guardrail: {ok, violations}, elapsed_ms, end_call: bool}` |

`end_call`은 `action`이 `ESCALATE` 또는 `OUT_OF_SCOPE`일 때 true. 알 수 없는 `call_id`는 404. LLM 호출 실패는 500과 오류 메시지로 그대로 드러낸다(조용히 이관으로 바꾸지 않는다).

## 5. 화면

### 5.1 상태 머신 (call.js)

```
IDLE ──[통화 버튼]──▶ RINGING(1.5초 벨소리) ──▶ start API ──▶ SPEAKING(인사말)
SPEAKING ──(TTS 끝)──▶ LISTENING
LISTENING ──(최종 인식 결과 또는 텍스트 전송)──▶ THINKING
THINKING ──(응답)──▶ SPEAKING(답변)
THINKING ──(1.5초 경과, 응답 없음)──▶ "잠시만 확인해 드리겠습니다" TTS 1회 후 계속 대기
SPEAKING ──(end_call == true, TTS 끝)──▶ ENDED
아무 상태 ──[끊기]──▶ ENDED (통화 시간, 턴 수 표시)
```

- LISTENING과 SPEAKING은 상호 배타다. TTS 중에는 인식을 끄고, 인식 중에는 TTS를 내보내지 않는다. 마이크가 스피커 소리를 다시 인식하는 문제를 이것으로 막는다.
- 인식 중간 결과는 화면에 회색으로 보여 준다.
- 텍스트 입력창은 통화중 항상 열려 있다. 마이크 권한 거부나 API 미지원 시 자동으로 텍스트 전용으로 전환하고 안내 문구를 띄운다.

### 5.2 음성 모듈 (voice.js)

노출 인터페이스 세 개만: `listen(): Promise<string>`, `speak(text): Promise<void>`, `stop(): void`. 내부는 Web Speech API(`ko-KR`). 한국어 음성이 여럿이면 `ko-KR` 중 첫 번째를 쓰고, 화면에서 고를 수 있게 선택 목록을 둔다. 서버 측 TTS로 바꿀 때는 이 파일만 교체한다.

### 5.3 관리자 패널 (panel.js)

턴마다 카드 하나. 고객 발화, 라우트와 확신도(임계값 미만이면 빨강), 호출한 도구와 인자, 가드레일 통과 여부와 위반 내용, 소요 시간. 이관·재시도·범위 밖은 빨간 배지.

## 6. 평가

- `eval/eval_router.py --domain modumall [--limit N] [--rule]`: eval split을 라우터에 통과시켜 정확도, macro F1, 분류 리포트, 혼동 행렬, 오분류 목록을 출력한다. `--rule`은 키워드 규칙 분류기로 기준선을 잰다.
- `eval/eval_answer.py --domain modumall [--limit N]`: 정답셋 각 대화의 첫 턴을 파이프라인에 통과시켜 action 혼동표, tools 미호출, must 누락, forbid 위반 건수와 통과율, 실패 목록을 출력한다. 라우트는 정답셋의 값을 그대로 쓴다(라우터 실패와 섞지 않기 위해). 실행 전에 정답셋의 모범 답안으로 채점기를 자기 검증하고, 하나라도 실패하면 중단한다.
- 두 스크립트 모두 `eval` split만 쓰고 `fewshot` split은 프롬프트에 넣지 않는다.

## 7. 테스트

LLM 호출 없이 도는 `pytest` 단위 테스트.

| 대상 | 검증 |
|---|---|
| guardrail | 조회 결과 기반 답변 통과, 하드코딩 숫자 위반, 산술 유도값 허용, 콤마 정규화, 툴 미호출 단정, 미확정값 확답 |
| context | 라우트별 장 선택, SHIPPING에 5·6장 부재, 부록 제외 |
| tools | 없는 ID 오류, `$`키 제거, 부족액 계산, null 유지, `search_product` 단일/다중/없음 |
| router.gate | 임계값 경계, OTHER 판정 |
| domain | 필수 파일·키 누락 시 명시적 오류 |
| pipeline | 가짜 분류기·가짜 답변기를 주입해 ESCALATE, OUT_OF_SCOPE, ASK, 가드레일 재시도 경로 |
| app | `call/start` → `turn` 왕복(가짜 파이프라인), 알 수 없는 call_id 404 |

수동 검증 체크리스트(README): "배송비 얼마예요?"에 되묻는가, 없는 상품을 있다고 하지 않는가, 검품 전 반품 배송비를 단정하지 않는가, 범위 밖을 안내하고 끊는가, 오타를 알아듣는가, 앞 턴 상품명을 뒤 턴이 이어받는가.

## 8. 범위에서 뺀 것

- 서버 측 음성 인식·합성 (Whisper, OpenAI TTS)
- 체크포인터 영속화 (DB), 다중 사용자 인증
- 고객 로그인·주문 이력 기반 식별자 자동 채우기
- 도구 집합이 다른 도메인
- Vercel 등 배포

## 9. 성공 기준

- 크롬에서 통화 버튼 → 인사말 → 음성 질문 → 음성 답변이 끊김 없이 한 바퀴 돈다.
- 수동 검증 체크리스트 6개 항목이 통과한다.
- `eval_router`가 모두몰 eval 120건에서 macro F1을 출력하고, `eval_answer`가 첫 턴 34건의 통과율과 실패 목록을 출력한다.
- `DOMAIN` 환경 변수만 바꿔 두 번째 도메인 폴더로 기동된다(두 번째 도메인 데이터 작성은 별도 작업).
- `pytest`가 API 키 없이 통과한다.
