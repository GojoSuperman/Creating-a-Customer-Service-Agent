# C단계: 쇼핑몰 고객 화면 설계

- 작성일: 2026-09-17
- 상태: 사용자 승인 (채팅에서 범위·구현 방식 확정 — 목록·장바구니·주문까지, 어드민과 같은 Jinja2)
- 선행: `2026-09-16-realistic-data-and-customer-id-design.md`(A단계 SQLite), `2026-09-17-admin-console-design.md`(B단계 어드민)
- 구현 방식: 계획은 Opus 컨트롤러, 구현은 Sonnet 서브에이전트, 리뷰는 Opus.

## 1. 목표

지금 `orders`·`order_items` 는 `server/db/generate.py` 가 만든 시드뿐이다. 고객이 직접 주문을 만드는 경로가 없어서, 데모에서 "방금 주문한 건"을 상담 에이전트에게 물어볼 수가 없다. 전화번호 로그인 → 상품 목록 → 장바구니 → 주문까지 되는 고객 화면을 붙여, **고객이 만든 주문이 그대로 어드민(`/admin/orders`)과 에이전트 도구(`get_order_status`)에 나타나게** 한다.

범위 밖(이번에 하지 않는 것):
- 결제 연동(주문은 즉시 `결제완료` 상태로 생성된다)
- 마이페이지의 반품·교환 신청(`returns` 쓰기)
- 회원가입·비밀번호·주소 변경(고객은 시드 데이터에 이미 있는 사람만 로그인)
- 지역별 추가 배송비(도서산간 등). 시드에는 3,000·5,500원 주문이 있지만 이번 주문 생성은 기본 배송비와 무료배송 임계값만 쓴다.
- 상품 이미지(이름·가격·옵션 텍스트만)

## 2. 구조

어드민과 대칭으로 만든다. 어드민의 읽기 전용 원칙은 그대로 두고, **쓰기는 `ShopRepo` 안에만** 둔다.

```
server/shop.py               /shop/* APIRouter, 세션·장바구니 쿠키 처리, 템플릿 렌더
server/shoprepo.py           상품 조회 + 주문 생성(이 프로젝트 최초의 쓰기 경로)
server/templates/shop/       base_shop.html + 목록·상세·장바구니·주문확인·완료·내주문·로그인
web/shop.css
```

- `server/app.py` 는 어드민과 같은 조건(`pipeline.repo` 존재)에서 `shop_router(pipeline.repo, domain)` 를 `include_router` 한다.
- 새 파이썬 의존성 없음(HMAC 서명은 표준 라이브러리 `hmac`/`hashlib`/`base64`).

### 2.1 라우트

| 경로 | 메서드 | 내용 |
|---|---|---|
| `/shop` | GET | 상품 목록. 필터 `category`, 검색 `q`(이름 부분일치), 페이지 24개 |
| `/shop/products/{product_id}` | GET | 상품 상세. 가격·옵션·소재·재고·품절 안내, 장바구니 담기 폼 |
| `/shop/cart` | GET | 장바구니. 수량 변경·삭제, 합계·배송비 미리보기 |
| `/shop/cart/add` | POST | 담기(`product_id`, `qty`, `option`) → `/shop/cart` 로 303 리다이렉트 |
| `/shop/cart/update` | POST | 수량 변경·삭제 → 303 |
| `/shop/checkout` | GET | 주문 확인(배송지·품목·금액). 비로그인이면 `/shop/login` 으로 |
| `/shop/checkout` | POST | **주문 생성** → `/shop/orders/{order_id}` 로 303 |
| `/shop/orders` | GET | 내 주문 목록(로그인 필요) |
| `/shop/orders/{order_id}` | GET | 내 주문 상세. 남의 주문이면 404 |
| `/shop/login` | GET/POST | 전화번호 입력 → 세션 쿠키 |
| `/shop/logout` | POST | 세션 쿠키 삭제 |

- 쓰기는 POST + 303 리다이렉트(Post/Redirect/Get)로 새로고침 재주문을 막는다.
- 없는 상품·주문은 어드민과 같은 방식의 HTML 404.

### 2.2 로그인과 세션

비밀번호는 없다(데모). 전화번호를 입력하면 `customers` 에서 찾고, 있으면 세션 쿠키를 굽는다.

- 쿠키 `shop_session` = `base64(customer_id)` + `.` + `HMAC-SHA256` 서명. 키는 환경변수 `SHOP_SECRET`, 없으면 프로세스 기동 시 `secrets.token_hex(32)` 로 만든다(재시작하면 로그아웃된다 — 로그에 한 줄 경고).
- 서명이 맞지 않거나 고객이 없으면 비로그인으로 취급한다.
- 쿠키 속성: `HttpOnly`, `SameSite=Lax`, `Path=/shop`. (로컬 HTTP 데모라 `Secure` 는 켜지 않는다.)
- 없는 번호면 "가입 이력이 없는 번호입니다" 안내 + 시드 고객 예시 3명 표시(데모 편의).
- **통화 화면과 세션을 공유하지 않는다.** 통화 중 말한 전화번호로 고객을 조회하지 않는 기존 규칙과 무관한 별개 경로다.

### 2.3 장바구니

서버 상태를 두지 않고 **서명 쿠키** 하나에 담는다.

- 쿠키 `shop_cart` = `base64(json)` + `.` + HMAC 서명. 값은 `[{"product_id","option","qty"}]`.
- 서명이 깨졌으면 빈 장바구니로 취급(오류 화면 없이).
- 최대 20줄, 줄당 수량 1~99. 초과 요청은 클램프.
- 로그인 전에도 담을 수 있고, 로그인해도 유지된다. 주문이 완료되면 비운다.

### 2.4 금액 계산

`server/tools.py` 가 쓰는 정책과 같은 출처를 읽는다 — 기본 배송비는 `domain.fixed_values["base_shipping_fee"]`(2,500원), 무료배송 임계값은 `categories.free_shipping_threshold`.

- 상품 합계 = Σ(price × qty)
- 배송비 규칙:
  1. 장바구니에 **화장품(COSMETICS)이 하나라도 있으면 무료배송 대상이 아니다** → 기본 배송비(정책 문서 4.1 기준).
  2. 그 외에는 포함된 모든 카테고리의 임계값을 상품 합계가 **전부 충족**해야 무료. 하나라도 못 미치면 기본 배송비.
  3. 임계값이 `NULL` 인 카테고리는 무료배송 대상이 아니다(현재는 화장품뿐).
- 계산은 `ShopRepo.quote(cart)` 한 곳에만 둔다. 장바구니·주문 확인·주문 생성이 모두 이 함수를 쓴다(값이 갈라지지 않게).

### 2.5 주문 생성

한 트랜잭션(`BEGIN IMMEDIATE` … `COMMIT`)으로 처리한다.

1. 장바구니의 각 상품을 다시 조회해 **품절(`soldout=1`)이거나 재고 부족이면 전체를 거부**하고 장바구니로 되돌려 사유를 보여준다(부분 주문 없음).
2. `order_id` 채번: `select max(order_id) from orders` 의 숫자 부분 + 1 → `O-####`. 같은 트랜잭션 안에서 뽑아 충돌을 막는다.
3. `orders` insert: `customer_id`, `ordered_at`=현재 ISO, `status`='결제완료', `order_amount`=상품 합계, `shipping_fee`, `free_shipping_applied`, `address_region`=고객의 `address_region`, 나머지 배송 열은 NULL.
4. `order_items` insert: 상품별 `name`·`option`·`qty`·`price`(주문 시점 가격을 박아 둔다).
5. `products.stock` 을 주문 수량만큼 차감(`stock` 이 NULL 인 상품은 건드리지 않는다). 차감 결과 0이면 `soldout` 은 건드리지 않는다(재입고 운영은 범위 밖).
6. 실패하면 롤백하고 아무것도 남기지 않는다.

`Repo` 의 커넥션을 공유하므로, 쓰기는 `Repo` 가 쓰는 것과 같은 락 아래에서 한다(어드민이 읽는 중 쓰기가 끼어드는 경우를 막는다).

### 2.6 다른 화면과의 연결

- 통화 화면(`web/index.html`) 헤더에 `/shop` 링크를 더한다(어드민 링크 옆).
- 쇼핑몰 헤더에는 `/admin` 링크를 두지 않는다(고객 화면이므로).
- 주문 완료 화면에 "상담원에게 문의" 문구와 함께 주문번호를 크게 보여준다 — 그 번호로 바로 통화 데모를 할 수 있다.

## 3. 테스트

`tests/test_shoprepo.py`, `tests/test_shop_routes.py` 를 새로 만든다. 기존 280개는 그대로 통과해야 한다.

- **쓰기 테스트는 공유 DB(`domains/modumall/modumall.db`)에 쓰지 않는다.** 인메모리 SQLite(`:memory:` + `server/db/schema.sql` + 필요한 행만 시드)로 한다. B단계에서 확립한 규칙과 같다.
- `ShopRepo.quote`: 임계값 미달/충족, 화장품 포함, 혼합 카테고리, 빈 장바구니.
- 주문 생성: 채번 형식, `orders`·`order_items` 행 내용, 재고 차감, 품절·재고 부족 거부, 실패 시 롤백(아무 행도 남지 않음), 가격은 주문 시점 값으로 박히는지.
- 세션·장바구니 쿠키: 서명 왕복, 변조된 쿠키는 비로그인/빈 장바구니로 처리, 로그아웃.
- 라우트: 목록 필터·검색·페이징, 상세 200/404, 담기 → 장바구니 반영, 비로그인 checkout → 로그인 유도, 주문 후 `/admin/orders` 목록에 그 주문이 보이는지(어드민과의 연결 확인), 남의 주문 상세는 404.
- XSS: 검색어·옵션 문자열이 이스케이프되는지.

## 4. 성공 기준

1. 전화번호로 로그인해 상품을 담고 주문하면 `/shop/orders` 와 `/admin/orders` 양쪽에 같은 주문이 보인다.
2. 그 주문번호로 에이전트에게 물으면 `get_order_status` 가 그 주문을 찾는다.
3. 배송비가 정책(기본 2,500원, 카테고리 임계값, 화장품 예외)대로 계산된다.
4. 품절 상품은 주문되지 않고, 실패한 주문은 DB에 흔적을 남기지 않는다.
5. pytest 전체 통과(기존 280 + 신규).
