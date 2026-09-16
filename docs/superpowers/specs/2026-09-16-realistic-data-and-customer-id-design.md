# A단계: 현실적인 데이터 기반과 고객 식별 설계

- 작성일: 2026-09-16
- 상태: 사용자 승인 (채팅에서 A → 강화 → B → C 순서 확정)
- 선행: 1차(음성 에이전트), 2차(평가 강화) — 모두 feat/voice-agent 브랜치에 구현 완료
- 후속: 모두몰 강화(프롬프트·LLM 심판) → B(어드민 화면) → C(쇼핑몰 고객 화면)

## 1. 목표

수업용 목 데이터(상품 20·주문 11·반품 5)를 실제 쇼핑몰 규모의 데이터로 바꾸고, 전화 발신번호로 고객을 식별해 "그 주문 언제 와요?" 같은 식별자 없는 문의를 처리한다. 수업이 지목한 가장 큰 병목(자동화율은 시스템이 쥔 고객 컨텍스트에 좌우된다)을 푸는 단계다.

## 2. 확정 사항

| 항목 | 결정 |
|---|---|
| 저장소 | SQLite 파일 `domains/modumall/modumall.db`. git에는 넣지 않고 생성 스크립트로 만든다(결정적 시드라 항상 같은 내용) |
| 규모 | 상품 약 180, 고객 120, 주문 약 450(최근 90일), 반품 약 60, 배송 이력, 재입고 예정 |
| 호환성 | 기존 `mockdb.json`의 상품 20·주문 11·반품 5·재입고 2는 **같은 ID와 값 그대로** DB에 들어간다. 정답셋·회귀 스위트·기존 테스트가 계속 유효해야 한다 |
| 고객 식별 | 통화 시작 시 발신번호를 받아 고객과 최근 주문을 찾고, 답변 프롬프트에 "통화 고객" 블록으로 넣는다 |
| 도구 | 기존 9개는 시그니처 유지, `find_customer` 1개 추가 |
| 화면 | 통화 화면에 발신번호 입력(샘플 고객 선택 가능) 추가, 패널에 식별된 고객 표시 |
| 기술 | Python 표준 `sqlite3`. ORM 없음 |

## 3. 데이터 모델

`server/db/schema.sql`:

| 테이블 | 열 (요지) |
|---|---|
| `categories` | key, label, free_shipping_threshold(null 가능), free_shipping_note, return_window_days, return_window_basis, requires_unopened |
| `same_day_delivery` | region, available, fee, extra_fee, note |
| `customers` | customer_id(C-0001…), name, phone(010-xxxx-xxxx, 유일), address_region(수도권/수도권외/제주도서산간), address, joined_at |
| `products` | product_id, name, category, price, stock, is_set, components(JSON), material, material_note, origin, has_quality_cert, made_to_order, made_to_order_days, options(JSON), size_chart(JSON), size_matching, individual_purchase_allowed, individual_purchase_note, individual_prices(JSON), return_allowed, return_blocked_reason, soldout, soldout_note, stock_note |
| `orders` | order_id, customer_id, ordered_at, status, status_detail, is_external_channel, external_channel_name, order_amount, shipping_fee, free_shipping_applied, address_region, courier, tracking_no, invoice_printed, expected_ship_date, shipped_at, expected_delivery, delivered_at, delay_days, delay_reason, return_id, note |
| `order_items` | order_id, product_id, name, option, qty, price |
| `shipment_events` | order_id, at, status, location |
| `returns` | return_id, order_id, type, return_scope, reason_stated, requested_at, stage, inspection_result, fault_party, shipping_fee_bearer, return_fee, refund_amount, refund_calc, expected_completion, exchange_target, exchange_available, exchange_blocked_reason, convert_to_refund, courier_visit_expected, note |
| `return_stage_history` | return_id, stage, date |
| `restock` | product_id, name, is_soldout, is_confirmed, expected_date, expected_note, notify_available |
| `call_logs` | call_id, customer_id, started_at, ended_at, turns(JSON) — B단계 어드민에서 읽는다 |

`$`로 시작하는 교육용 주석 키는 DB에 넣지 않는다.

## 4. 생성 스크립트 `server/db/generate.py`

- 입력: `mockdb.json`(정식 데이터 seed), 시드 고정 난수(`random.Random(20260916)`), 기준일 2026-09-16.
- 순서: 스키마 생성 → 카테고리·특수배송 → mockdb의 상품·주문·반품·재입고 그대로 삽입 → 정식 주문 11건에 고객 8명 배정(이름·전화 생성) → 추가 고객 112명 → 추가 상품 160개(6개 카테고리, 이름 목록×수식어 조합, 옵션·실측·소재 템플릿) → 추가 주문 약 440건(최근 90일 균등, 상태 분포: 배송완료 55% · 배송중 15% · 결제완료 10% · 반품진행 8% · 교환진행 4% · 제작중 3% · 배송지연 5%) → 반품 약 55건(주문 상태와 일치, 단계별 이력) → 배송 이력 → 재입고 8건.
- 무료배송·배송비는 카테고리 기준액으로 계산해 주문에 기록(도구가 답할 때와 어긋나지 않게).
- `python -m server.db.generate --domain modumall [--force]`로 실행. DB가 없으면 도메인 로드 시 자동 생성.
- 결정성 테스트: 같은 시드로 두 번 생성한 DB의 행 수와 정식 ID 값이 같다.

## 5. 저장소 계층 `server/repo.py`

`Repo` 클래스(sqlite3 연결 하나, 읽기 전용 메서드): `product(pid)`, `products()`, `order(oid)`(items 포함), `return_by_id(rid)`, `return_by_order(oid)`, `restock(pid)`, `categories()`, `same_day()`, `customer_by_phone(phone)`, `customer(cid)`, `recent_orders(cid, limit=3)`, `shipment_events(oid)`, `log_call(...)`. 반환은 기존 도구가 쓰던 dict 모양과 동일(JSON 열은 파싱).

`Domain`에 `db_path: Path` 추가. `make_tools(domain)`은 `Repo(domain.db_path)`를 열고, 기존 dict 조회를 Repo 호출로 바꾼다. `search_product`의 후보 목록은 `products()`를 시작 시 한 번 읽어 메모리에 둔다(180개).

## 6. 고객 식별

### 6.1 도구 `find_customer`

```
find_customer(phone: Optional[str] = None, order_id: Optional[str] = None) -> dict
```
전화번호(하이픈 유무 무관)로 고객을 찾고 `{customer_id, name, address_region, recent_orders: [{order_id, ordered_at, status, items_summary, order_amount}]}`를 돌려준다. 없으면 `{"error": "고객을 찾을 수 없습니다"}`. `order_id`로도 역조회 가능.

### 6.2 통화 시작

- `POST /api/call/start {phone?: str}` → `{call_id, greeting, customer: {name, recent_orders} | null}`.
- `Pipeline.start_call(phone=None)`: 고객을 찾으면 통화 컨텍스트(`self.customers[call_id]`)에 저장하고 인사말을 "홍길동 고객님, 안녕하세요. 모두몰 고객센터입니다. 무엇을 도와드릴까요?"로 만든다(`domain.json`의 `greeting_known: "{name} 고객님, 안녕하세요. …"`). 최근 14일 내 미배송 주문이 있으면 "9월 12일 주문하신 캔버스화 건이신가요?"를 덧붙인다.
- 답변 프롬프트: `Answerer.answer(..., customer: dict | None)`가 시스템 프롬프트 끝에 `===== 통화 고객 =====` 블록(이름, 최근 주문 3건)을 넣는다. 규칙 추가: "통화 고객의 최근 주문이 하나뿐이고 고객이 '그 주문', '어제 산 거'처럼 말하면 그 주문번호로 조회한다. 여럿이면 어느 주문인지 되묻는다."
- 라우터는 변경 없음.
- 가드레일: 통화 고객 블록의 숫자(주문 금액 등)를 허용 집합에 포함한다(`tool_results`에 `"_customer"` 키로 합쳐 검사).
- 비회원(번호 없음)이면 지금과 동일하게 동작한다.

### 6.3 화면

- 통화 버튼 옆에 "발신 번호" 입력. `/api/domain`이 `sample_customers: [{name, phone}] 5명`을 주면 드롭다운으로 골라 채울 수 있다. 비워 두면 비회원.
- 패널 상단에 식별된 고객 카드(이름, 최근 주문 3건).
- 통화 종료 시 `call_logs`에 턴 기록 저장(B단계용).

## 7. 매뉴얼·프롬프트

- `policy.md` §8에 한 단락 추가: "발신번호로 고객이 확인되면 이름으로 인사하고 최근 주문을 먼저 짚는다. 확인되지 않으면 주문번호를 묻는다. 다른 사람의 주문 정보는 안내하지 않는다."
- `ANSWER_RULES` 절차에 0단계 추가: "[통화 고객] 블록이 있으면 그 주문번호를 우선 사용한다."

## 8. 평가

- 기존 `eval_router`, `eval_answer`, `eval_hard`, `eval_regression`은 그대로 동작해야 한다(정식 데이터 보존).
- 새 스크립트 `eval/eval_context.py`: 수업의 "실제 문의 40건 흘려보내기"를 재현하되 두 조건으로 비교한다 — 비회원 vs 식별된 고객(각 문의에 무작위 고객을 붙임). 출력: 자동화율(답변 도달), 되묻기율, 조회율. 이 숫자가 "고객 컨텍스트가 자동화율을 올린다"는 수업의 주장을 검증한다.

## 9. 테스트

- 생성기: 결정성, 정식 ID 보존(P1001~P6003, O-1001~O-1011, R-2001~R-2005 값 일치), 규모 하한, 주문 금액 = 항목 합 + 배송비, 반품 단계 이력 순서.
- Repo: 각 메서드가 mockdb 기반 기대값과 같은 dict를 돌려준다(예: `order("O-1006")`의 `return_id == "R-2001"`).
- tools: 기존 25개 테스트 전부 통과 + `find_customer` 전화번호 정규화·미존재.
- pipeline: 식별된 고객으로 시작한 통화에서 답변기가 customer 블록을 받는지, 비회원이면 None인지.
- app: `call/start`에 phone 전달, 잘못된 번호는 customer null.
- guardrail: customer 블록 숫자 허용.

## 10. 범위에서 뺀 것

- 로그인·본인 인증(전화번호만으로 식별. 실제 서비스면 추가 인증 필요하다고 매뉴얼에 명시)
- 어드민 화면(B), 쇼핑몰 화면(C), 주문 생성 API(C에서)
- 임베딩 기반 상품 검색

## 11. 성공 기준

- `python -m server.db.generate`가 10초 안에 끝나고 두 번 실행해도 같은 내용.
- `pytest` 키 없이 전부 통과(기존 129 + 신규).
- 정답셋 채점기 자기 검증 34건 통과(정식 데이터 보존 확인).
- 통화 화면에서 샘플 고객 번호로 시작하면 이름으로 인사하고, "그 주문 언제 와요?"에 주문번호 없이 답한다.
- `eval_context.py`가 비회원 대비 식별 고객의 자동화율 상승을 숫자로 보여 준다.
