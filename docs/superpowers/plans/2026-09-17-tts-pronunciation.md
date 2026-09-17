# TTS 한국어 발음 변환 구현 플랜

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 상담원이 읽는 문장을 한국어 발음 관행에 맞게 바꿔, `14K` 가 "일사케이" 로, `3개` 가 "삼개" 로 들리는 문제를 없앤다.

**Architecture:** 서버에 순수 함수 모듈 `server/pronounce.py` 를 두고(파이썬이라 테스트로 촘촘히 고정할 수 있다), 통화 API 응답에 **화면용 원문(`answer`)과 낭독용 문장(`speech`)을 따로** 내려준다. 브라우저 음성·서버 TTS 양쪽이 같은 `speech` 를 쓴다. 화면 표시는 바뀌지 않는다.

**Tech Stack:** Python 3.12(표준 라이브러리만), FastAPI, 바닐라 JS, pytest

**Spec:** 별도 스펙 문서 없음 — 이 플랜 상단의 조사 결과와 사용자 승인 사항(아래)이 스펙이다. 조사 원본: `.superpowers/조사/2026-09-17-TTS-발음-전수조사.md`

## 사용자가 승인한 결정

1. **화면 텍스트는 그대로 두고 음성만 바꾼다.**
2. **주문번호·전화번호·송장번호는 자릿수 그대로** 읽는다("오 일공칠이").
3. **상품명 끝 숫자(품번)는 "N번"** 으로 읽는다 — `14K 도금 커프 링 2` → "십사케이 도금 커프 링 2번".
4. **소재·단위는 한국어 관행대로**: `14K`→"십사케이", `925 실버`→"구이오 실버", `80%`→"팔십 퍼센트", `2,500원`→"이천오백 원", `7종`→"칠종", `2026-09-14`→"9월 14일".

## 조사에서 확인된 사실 (전수 스캔)

- 상품명에 영문이 들어간 상품 7개, 전부 `14K` 패턴. material 필드에 `14K`(11건)·`925 실버`(6건)·`PU`(4건).
- **상품명 끝 숫자는 수량이 아니라 품번**: `order_items` 998건 중 166건(`데님 머플러 2`, `실버 커프 링 2`). 실제 사례 O-1351 은 `name="14K 도금 커프 링 2", qty=3` — 이름의 2와 수량 3이 별개다. 이것이 사용자가 겪은 오발음의 근본 원인.
- 수량 분포: 1개 830건, 2개 126건, 3개 42건 — 전부 소수라 고유어 수사로 읽을 수 있다.
- 정답셋 답변 56건 중 금액 17건, 기간 7건, ISO 날짜 3건, 시각 2건.
- `shipment_events.at` 은 `2026-08-21T12:30:00` 형태라 그대로 읽히면 최악이다.
- 사이즈: 의류 `S/M/L/XL/2XL`, 신발 `230~270`, 반지 `9호~21호`, 브라 `75A~85A`, 길이 `40cm`.

## Global Constraints

- **새 파이썬 의존성 금지.** 표준 라이브러리만.
- **화면 표시 문자열을 바꾸지 않는다.** 변환 결과는 낭독에만 쓴다.
- `server/repo.py`·`server/adminrepo.py`·`server/shoprepo.py`·`server/admin.py`·`server/shop.py` 수정 금지.
- 변환은 **순수 함수**로 두고(입력 문자열 → 출력 문자열), DB 접근이 필요한 부분(품번 카탈로그)은 인자로 주입받는다.
- 주석·문서 한국어, 식별자 영어.
- 기존 테스트 407개가 계속 통과: `.venv/bin/python -m pytest -q`
- `tests/test_generate.py` 가 공유 DB 를 재생성한다는 점에 유의(평가가 돌고 있으면 기다릴 것).
- 커밋 메시지에 Co-Authored-By 트레일러 금지.

---

### Task 1: 발음 변환 핵심 규칙

**Files:**
- Create: `server/pronounce.py`
- Test: `tests/test_pronounce.py`

**Interfaces:**
- Produces: `to_speech(text: str, product_names: list[str] | None = None) -> str` — 낭독용 문장을 돌려준다. `product_names` 는 Task 2 에서 쓰며 이번 태스크에서는 무시해도 된다(인자만 받아 둘 것).

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_pronounce.py`

```python
import pytest

from server.pronounce import to_speech


@pytest.mark.parametrize("raw, spoken", [
    # 영문+숫자 소재·규격
    ("14K 도금 커프 링", "십사케이 도금 커프 링"),
    ("925 실버 목걸이", "구이오 실버 목걸이"),
    ("소재는 PU 입니다", "소재는 피유 입니다"),
    ("SPF 50 자외선 차단", "에스피에프 50 자외선 차단"),
    # 길이·용량 단위
    ("어깨 45cm, 가슴 106cm", "어깨 45센티미터, 가슴 106센티미터"),
    ("토너 200ml", "토너 200밀리리터"),
    # 퍼센트
    ("폴리에스터 80%, 코튼 20%", "폴리에스터 80 퍼센트, 코튼 20 퍼센트"),
    # ISO 날짜·시각
    ("2026-08-24 출고 예정입니다", "8월 24일 출고 예정입니다"),
    ("2026-08-21T12:30:00 에 접수", "8월 21일 12시 30분 에 접수"),
    # 수량은 고유어 + 단위
    ("3개 주문하셨습니다", "세 개 주문하셨습니다"),
    ("1개 남았습니다", "한 개 남았습니다"),
    ("20개까지 가능합니다", "스무 개까지 가능합니다"),
    # 한자어로 읽는 단위는 그대로 둔다
    ("7종 세트", "7종 세트"),
    ("5매 입니다", "5매 입니다"),
    ("반품 기한은 7일입니다", "반품 기한은 7일입니다"),
])
def test_basic_rules(raw, spoken):
    assert to_speech(raw) == spoken


def test_money_and_plain_numbers_are_left_alone():
    """금액·기간은 한국어 TTS 가 이미 제대로 읽으므로 건드리지 않는다."""
    assert to_speech("배송비 2,500원이 발생합니다") == "배송비 2,500원이 발생합니다"
    assert to_speech("3~4영업일 소요됩니다") == "3~4영업일 소요됩니다"
    assert to_speech("9월 1일 입고 예정입니다") == "9월 1일 입고 예정입니다"


@pytest.mark.parametrize("raw, spoken", [
    ("주문번호 O-1072 입니다", "주문번호 오 일공칠이 입니다"),
    ("반품번호 R-2013", "반품번호 알 이공일삼"),
    ("고객번호 C-0062", "고객번호 씨 공공육이"),
])
def test_ids_are_read_digit_by_digit(raw, spoken):
    assert to_speech(raw) == spoken


def test_tracking_and_phone_are_read_digit_by_digit():
    assert to_speech("송장번호는 621312907791 입니다").startswith("송장번호는 육이일삼")
    assert "공일공" in to_speech("010-3711-0062 로 연락드립니다")


def test_size_letters():
    assert to_speech("L 사이즈는 가슴 112cm") == "엘 사이즈는 가슴 112센티미터"
    assert to_speech("2XL 까지 있습니다") == "투엑스엘 까지 있습니다"
    assert to_speech("브라 80A 선택 시") == "브라 80에이 선택 시"


def test_is_idempotent():
    """두 번 돌려도 같은 결과여야 한다 (중복 적용 방지)."""
    once = to_speech("14K 도금 커프 링 3개, 45cm")
    assert to_speech(once) == once


def test_empty_and_plain_text():
    assert to_speech("") == ""
    assert to_speech("안녕하세요, 모두몰입니다.") == "안녕하세요, 모두몰입니다."
```

**주의**: 위 기대값 중 일부는 구현하면서 조정될 수 있다(예: 공백 위치). **기대값을 바꿀 때는 왜 바꿨는지 보고서에 쓰고, "한국어로 들었을 때 자연스러운가" 를 기준으로 판단**할 것. 다만 `14K`→"십사케이", `3개`→"세 개", ISO 날짜 변환, 식별자 자릿수 읽기는 **사용자 승인 사항이라 바꾸지 말 것.**

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_pronounce.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'server.pronounce'`

- [ ] **Step 3: 구현** — `server/pronounce.py`

규칙은 **적용 순서가 중요하다**(식별자를 먼저 처리해야 그 안의 숫자가 다른 규칙에 걸리지 않는다). 아래 뼈대를 따르되 세부는 테스트에 맞춰 완성할 것.

```python
# -*- coding: utf-8 -*-
"""낭독용 문장 변환. 화면에 보이는 원문은 그대로 두고, 음성으로 읽을 때만 쓴다.

한국어 TTS 가 이미 잘 읽는 것(금액, N월 N일, 영업일)은 건드리지 않고,
잘못 읽는 것만 고친다 — 영문+숫자 조합, ISO 날짜, 수량의 고유어 수사, 식별자.
"""
import re

# 자릿수 그대로 읽을 숫자
DIGITS = {"0": "공", "1": "일", "2": "이", "3": "삼", "4": "사",
          "5": "오", "6": "육", "7": "칠", "8": "팔", "9": "구"}
# 식별자 접두 알파벳
ID_PREFIX = {"O": "오", "R": "알", "P": "피", "C": "씨"}
# 수량에 쓰는 고유어 수사
NATIVE = {1: "한", 2: "두", 3: "세", 4: "네", 5: "다섯", 6: "여섯", 7: "일곱",
          8: "여덟", 9: "아홉", 10: "열", 20: "스무"}
# 영문 약어·단위
UNITS = [("cm", "센티미터"), ("mm", "밀리미터"), ("ml", "밀리리터"), ("kg", "킬로그램")]
ABBREV = [("SPF", "에스피에프"), ("PU", "피유")]
SIZE_LETTERS = {"S": "에스", "M": "엠", "L": "엘", "XL": "엑스엘", "2XL": "투엑스엘"}
```

핵심 변환 함수들(각각 작은 함수로 나누고 `to_speech` 가 순서대로 부른다):
1. `_ids(text)` — `O-1072`·`R-2013`·`C-0062`·`P1001` 을 접두 알파벳 + 자릿수 읽기로.
2. `_long_digits(text)` — 전화번호(`010-3711-0062`)와 10자리 이상 숫자(송장번호)를 자릿수 읽기로.
3. `_iso_datetime(text)` — `2026-08-21T12:30:00` → "8월 21일 12시 30분", `2026-08-24` → "8월 24일". 연도는 읽지 않는다(상담에서 당해 연도가 자명하다). **연도가 올해와 다르면 "2027년 1월 5일" 처럼 연도를 붙인다** — 오늘 날짜는 인자로 주입받아 테스트가 고정되게 할 것.
4. `_alnum_material(text)` — `14K`→"십사케이"(숫자는 한자어 그대로 읽히므로 숫자는 두고 `K`만 "케이"로), `925 실버`→"구이오 실버", `80A`→"80에이".
5. `_units(text)` — `cm`·`ml` 등 단위 영문을 한글로.
6. `_percent(text)` — `80%` → "80 퍼센트".
7. `_size_letters(text)` — 단독으로 쓰인 `S`·`M`·`L`·`XL`·`2XL` 을 한글로. **주변에 한글이 붙은 경우만**(예: "L 사이즈") 바꿔 오탐을 줄인다.
8. `_counts(text)` — `3개` → "세 개". 1~10·20 만 고유어로 바꾸고 그 외는 그대로 둔다.

`to_speech` 는 위를 순서대로 적용하고, **두 번 돌려도 같은 결과**가 되도록(멱등) 각 정규식이 이미 변환된 결과에 다시 걸리지 않게 할 것.

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_pronounce.py -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add server/pronounce.py tests/test_pronounce.py
git commit -m "feat: 낭독용 문장 변환 규칙 (영문 단위·ISO 날짜·수량·식별자)"
```

---

### Task 2: 상품 품번을 "N번" 으로

**Files:**
- Modify: `server/pronounce.py`, `tests/test_pronounce.py`

**Interfaces:**
- Consumes: Task 1 의 `to_speech(text, product_names=None)`
- Produces: `product_names` 가 주어지면 그 이름들을 먼저 치환한다. 이름이 숫자로 끝나면 그 숫자를 "N번" 으로 읽는다.

- [ ] **Step 1: 실패하는 테스트 추가**

```python
CATALOG = ["14K 도금 커프 링 2", "14K 도금 커프 링", "데님 머플러 2", "코튼 니트 가디건"]


def test_product_trailing_number_is_read_as_model_number():
    """상품명 끝 숫자는 수량이 아니라 품번이다 (order_items 998건 중 166건)."""
    out = to_speech("14K 도금 커프 링 2 3개 주문하셨습니다", product_names=CATALOG)
    assert "커프 링 2번" in out
    assert "세 개" in out          # 수량은 여전히 고유어로
    assert "커프 링 두" not in out  # 품번을 수량으로 읽지 않는다


def test_longer_product_name_wins():
    """'커프 링' 과 '커프 링 2' 가 둘 다 있으면 긴 쪽을 먼저 맞춘다."""
    out = to_speech("14K 도금 커프 링 2 를 샀습니다", product_names=CATALOG)
    assert "커프 링 2번" in out


def test_product_without_trailing_number_untouched():
    out = to_speech("코튼 니트 가디건 2개", product_names=CATALOG)
    assert "가디건 2번" not in out
    assert "두 개" in out


def test_no_catalog_falls_back_to_plain_rules():
    out = to_speech("14K 도금 커프 링 2 3개")
    assert out.startswith("십사케이")
```

- [ ] **Step 2: 실패 확인** → **Step 3: 구현**

`to_speech` 맨 앞에서 `product_names` 를 **길이 내림차순**으로 정렬해 순회하며, 문장에 그 이름이 있으면 낭독형으로 치환한다(이름이 숫자로 끝나면 그 숫자 뒤에 "번" 을 붙인다). 치환한 구간은 뒤 규칙이 다시 건드리지 않도록 표시해 두거나, 치환 결과가 멱등이 되게 만들 것.

- [ ] **Step 4: 통과 확인 · Step 5: 커밋**

```bash
git add server/pronounce.py tests/test_pronounce.py
git commit -m "feat: 상품명 끝 품번을 'N번' 으로 읽기"
```

---

### Task 3: 서버 배선 — 응답에 낭독용 문장 싣기

**Files:**
- Modify: `server/app.py`, `server/pipeline.py`(필요한 최소 범위), `server/tts.py`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: Task 1·2 의 `to_speech`
- Produces: `/api/call/start` 와 `/api/call/turn` 응답에 `speech` 필드 추가(화면용 `greeting`·`answer` 는 그대로). `/api/tts` 는 받은 텍스트를 변환해 OpenAI 에 보낸다.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_app.py` 에 추가

```python
def test_turn_response_has_speech_field(client):
    s = client.post("/api/call/start").json()
    assert "speech" in s                      # 인사말도 낭독용을 함께 준다
    r = client.post("/api/call/turn", json={"call_id": s["call_id"], "text": "14K 반지 있어요?"}).json()
    assert r["answer"]                        # 화면용 원문은 그대로
    assert "speech" in r and r["speech"]
```

`FakePipeline` 이 돌려주는 답변에 `14K` 같은 변환 대상이 들어가도록 테스트 픽스처를 맞추고, **`answer` 는 원문 그대로이고 `speech` 만 변환됐음**을 단언할 것.

- [ ] **Step 2: 실패 확인 → Step 3: 구현**

- 상품명 카탈로그는 `repo.products()` 에서 이름만 뽑아 **한 번만 읽어 캐시**한다(요청마다 DB 를 훑지 않게).
- `/api/tts` 는 받은 텍스트에 `to_speech` 를 적용한 뒤 합성한다.
- 변환에서 예외가 나더라도 **통화가 끊기면 안 된다** — 실패 시 원문을 그대로 쓰고 경고만 남길 것. 이 폴백을 테스트로 덮을 것.

- [ ] **Step 4: 전체 테스트 · Step 5: 커밋**

---

### Task 4: 화면 — 낭독용 문장 사용

**Files:**
- Modify: `web/call.js`, `web/voice.js`
- Test: 없음(브라우저 코드). 대신 수동 확인 절차를 보고서에 남길 것.

- [ ] **Step 1: 구현**

- `call.js` 가 응답의 `speech` 를 받아 음성 재생에 넘긴다. 없으면 기존처럼 `answer` 를 쓴다(하위 호환).
- `voice.js` 의 기존 `speakable()`(식별자 자릿수 읽기)은 **서버 변환과 중복되지 않게** 정리한다 — 서버가 준 `speech` 를 쓸 때는 다시 적용하지 않는다.
- 화면 말풍선에는 계속 원문(`answer`)을 표시한다.

- [ ] **Step 2: 수동 확인**

서버를 8010 이 아닌 포트에 띄워 `/api/call/turn` 응답에 `speech` 가 오는지 curl 로 확인하고, 브라우저 실제 발음은 **사용자 확인 항목**으로 남긴다.

- [ ] **Step 3: 커밋**

---

### Task 5: 검증

- [ ] **Step 1: 실제 통화로 확인**

키가 있는 환경에서 통화를 걸어 `14K` 가 들어간 상품을 묻고, 응답의 `speech` 필드 원문을 보고서에 붙인다. 최소 3가지 표현으로 확인할 것.

- [ ] **Step 2: 회귀**

`.venv/bin/python -m pytest -q` 전체 통과. `eval_regression` 도 한 번 돌려 통화 경로가 깨지지 않았는지 본다(다른 평가가 돌고 있지 않을 때).

- [ ] **Step 3: 기록**

`docs/측정기록/` 에 짧은 기록을 남기고 README 에 한 줄 추가(발음 변환 계층이 생겼다는 사실과 적용 범위).

---

## 자기 점검 (플랜 작성자용)

- 승인 사항 4개 → 결정 1(화면 유지)은 Task 3·4, 결정 2(식별자)는 Task 1, 결정 3(품번 N번)은 Task 2, 결정 4(한국어 관행)는 Task 1.
- 조사에서 나온 패턴 7종 → Task 1 의 변환 함수 8개가 각각 대응.
- 품번과 수량의 충돌(핵심 발견) → Task 2 가 카탈로그 기반으로 해결하고, 테스트가 "커프 링 두" 가 나오지 않음을 단언.
- 브라우저·서버 TTS 양쪽 적용 → Task 3 이 한 곳(`speech`)에서 만들고 Task 4 가 그것만 쓰게 한다.
