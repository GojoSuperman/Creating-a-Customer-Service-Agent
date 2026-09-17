# B단계: 어드민 조회 화면 설계

- 작성일: 2026-09-17
- 상태: 사용자 승인 (채팅에서 범위·페이지 구성·인증 방식 확정)
- 선행: `2026-09-16-realistic-data-and-customer-id-design.md`(A단계 SQLite), `2026-09-17-modumall-hardening-design.md`(3차). 둘 다 `feat/voice-agent` 에 구현 완료.
- 구현 방식: 계획은 Opus 컨트롤러가 쓰고, 코드 구현은 Sonnet 서브에이전트에 태스크 단위로 위임, Opus 가 리뷰한다.

## 1. 목표

지금 `domains/modumall/modumall.db` 안의 주문·반품·통화 로그는 sqlite CLI 로만 볼 수 있다. 통화를 한 뒤 "에이전트가 뭘 보고 저렇게 답했나"를 확인하려면 DB를 직접 열어야 한다. Jinja2 서버 렌더 페이지로 **읽기 전용** 조회 화면을 붙여, 통화 직후 브라우저에서 데이터와 통화 로그를 확인할 수 있게 한다.

범위 밖(이번에 하지 않는 것):
- 데이터 수정·삭제(주문 상태 변경, 반품 단계 변경 등 쓰기 경로 전부)
- 로그인·권한(로컬 데모 전제, 인증 없음)
- 상품·재입고·카테고리 전용 페이지(주문 상세에서 품목으로만 보인다)
- 차트·통계 대시보드(홈의 숫자 4개까지만)

## 2. 구조

새 모듈 3개와 템플릿 디렉터리 하나를 더한다. 기존 통화 경로(`pipeline`, `tools`, `router`)는 건드리지 않는다. 예외는 2.5 의 턴 로그 한 줄.

```
server/admin.py        APIRouter. /admin/* 라우트, 쿼리 파라미터 파싱, 템플릿 렌더
server/adminrepo.py    AdminRepo. 목록·집계·페이징 SQL 전용 (읽기 전용)
server/templates/      base.html + _paging.html + 페이지 템플릿 10개
```

- `server/app.py` `create_app()` 에서 `pipeline.repo` 가 있을 때만 `admin_router(pipeline.repo)` 를 `include_router` 한다. DB 가 없는 도메인(목 JSON만 있는 경우)에서는 어드민을 붙이지 않는다.
- 의존성: `jinja2>=3.1` 을 `requirements.txt` 에 추가. FastAPI 의 `Jinja2Templates` 를 쓴다.
- 스타일은 `web/style.css` 와 별개로 `web/admin.css` 하나를 새로 만들고 `/static/admin.css` 로 서빙한다(이미 `/static` 마운트 존재). JS 없음.

### 2.1 라우트

| 경로 | 내용 |
|---|---|
| `GET /admin` | 요약 홈. 오늘 통화 수, 진행중 주문 수, 반품 단계별 건수, 최근 통화 5건. 각 목록으로 링크 |
| `GET /admin/orders` | 주문 목록. 필터 `status`, `customer_id`, `from`, `to`. 정렬 `ordered_at DESC`. 페이지 20 |
| `GET /admin/orders/{order_id}` | 품목, 배송 이벤트 타임라인, 연결된 반품, 지연 사유, 고객 링크 |
| `GET /admin/returns` | 반품 목록. 필터 `stage`, `type`. 정렬 `requested_at DESC`. 페이지 20 |
| `GET /admin/returns/{return_id}` | 단계 이력, 환불 산정(`refund_calc`), 교환 대상, 원 주문 링크 |
| `GET /admin/calls` | 통화 목록. 정렬 `started_at DESC`. 고객·시작·종료·턴 수·라우트 요약. 페이지 20 |
| `GET /admin/calls/{call_id}` | 턴별 카드: 질문·답변·라우트·확신도·액션·호출 도구·가드레일 |
| `GET /admin/customers` | 고객 목록. 검색 `q`(이름 부분일치 또는 전화 정규화 일치). 페이지 20 |
| `GET /admin/customers/{customer_id}` | 프로필, 주문 이력(전체), 통화 이력 |

- 존재하지 않는 id 는 `HTTPException(404)` 가 아니라 **404 상태 + 한국어 안내 HTML** 을 돌려준다(`templates/notfound.html`). 브라우저로 보는 화면이므로 JSON 오류는 쓰지 않는다.
- 필터 값은 화이트리스트 검증 없이 그대로 파라미터 바인딩한다(SQL 은 전부 `?` 바인딩, 문자열 조립 금지). 알 수 없는 값이면 결과 0건으로 나오면 된다.
- 페이지 번호는 `page`(1부터). 범위를 벗어나면 빈 목록 + "결과 없음".

### 2.2 AdminRepo

`Repo` 는 에이전트 도구가 쓰는 dict 모양(도구 계약)이라 건드리지 않는다. 어드민 전용 조회는 새 클래스에 둔다. `Repo` 의 `_row`/`_all`/`_one` 헬퍼와 `normalize_phone` 은 재사용한다(중복 구현 금지).

```python
class AdminRepo:                       # server/adminrepo.py
    def __init__(self, con)            # Repo 와 같은 sqlite3 연결을 공유 (Repo.con 주입)
    def summary(self, today_iso)       # {"calls_today", "orders_in_progress", "returns_by_stage": [...]}
    def orders(self, *, status=None, customer_id=None, date_from=None, date_to=None, page=1, size=20)
    def order_detail(self, order_id)   # 주문 + items + events + return + customer
    def returns(self, *, stage=None, type=None, page=1, size=20)
    def return_detail(self, return_id) # 반품 + stage_history + order
    def calls(self, *, page=1, size=20)
    def call_detail(self, call_id)     # call + turns(파싱) + customer
    def customers(self, *, q=None, page=1, size=20)
    def customer_detail(self, customer_id)  # customer + orders 전체 + calls
```

- 목록 메서드는 `(rows, total)` 을 돌려준다. `total` 은 같은 조건의 `count(*)`.
- "진행중 주문"의 정의: `status` 가 `배송완료` 도 아니고 반품/취소 종결도 아닌 건. 구현 시 DB 의 실제 `status` 값 분포를 먼저 확인해 목록을 확정하고, 상수 `IN_PROGRESS_STATUSES` 로 모듈 상단에 둔다.
- "오늘 통화": `started_at` 의 날짜 부분이 `today_iso[:10]` 인 건.

### 2.3 템플릿

`base.html` 에 상단 네비(홈·주문·반품·통화·고객)와 `{% block %}`. 나머지는 홈 1개·목록 4개·상세 4개·notfound 1개, 페이지네이션 부분 템플릿 `_paging.html` 1개.

- 자동 이스케이프를 켠다(`Jinja2Templates` 기본값이 켜짐 — 끄지 않는다). `|safe` 는 어디에도 쓰지 않는다.
- 날짜·금액 표기는 기존 `web/panel.js` 규칙을 따른다: `9월 17일`, `12,000원`.
- 목록 페이지네이션은 "이전 / n / 다음" 링크. 현재 필터를 쿼리스트링으로 유지한다.

### 2.4 기존 화면과의 연결

`web/index.html` 헤더에 `/admin` 링크 하나를 추가한다(새 탭). 통화 화면의 동작은 바꾸지 않는다.

### 2.5 턴 로그에 답변·확신도 추가

`server/pipeline.py:382` 의 턴 로그에 `"a": out["answer"]`, `"confidence": out.get("confidence")` 두 필드를 더한다. 지금은 질문·라우트·액션·도구·가드레일만 저장돼 통화 상세가 반쪽이 된다.

- 과거에 쌓인 로그에는 이 두 키가 없다. 템플릿은 키가 없으면 "기록 없음"으로 표시한다(마이그레이션 없음).
- 이 변경으로 `call_logs.turns` JSON 이 커지지만 로컬 SQLite 라 문제 없다.

## 3. 테스트

`tests/test_adminrepo.py`, `tests/test_admin_routes.py` 를 새로 만든다. 기존 241개는 그대로 통과해야 한다.

- 픽스처: `server/db/generate.py` 로 임시 디렉터리에 DB 를 만들거나, 기존 `tests/conftest.py` 의 DB 픽스처가 있으면 재사용한다(구현자가 먼저 확인).
- `AdminRepo` 단위 테스트: 필터별 건수, 페이징 경계(1페이지/마지막/초과), 검색 `q` 의 이름·전화 양쪽 매칭, `summary` 집계값, 없는 id 는 `None`.
- 라우트 테스트(TestClient): 9개 경로 200, 없는 id 는 404 + 안내 문구, 목록 필터가 쿼리스트링으로 살아 있는지, 페이지 링크 존재.
- XSS: 고객 이름에 `<script>` 를 넣은 픽스처로 응답 본문에 `&lt;script&gt;` 로 나오는지 확인.
- 쓰기 금지 회귀: `server/adminrepo.py` 소스에 `insert|update|delete` SQL 키워드가 없음을 확인하는 테스트 1개.

## 4. 성공 기준

1. `PORT=8010 .venv/bin/python -m server` 로 띄운 뒤 `/admin` 에서 4개 목록과 상세로 링크만 따라가 이동할 수 있다.
2. 통화를 한 건 끝낸 뒤 `/admin/calls` 최상단에 그 통화가 보이고, 상세에서 턴별 질문·답변·라우트·도구가 보인다.
3. pytest 전체 통과(기존 241 + 신규).
4. 어드민 코드에 쓰기 SQL 이 없다.
