# -*- coding: utf-8 -*-
"""낭독용 문장 변환. 화면에 보이는 원문은 그대로 두고, 음성으로 읽을 때만 쓴다.

한국어 TTS 가 이미 잘 읽는 것(금액, N월 N일, 영업일)은 건드리지 않고,
잘못 읽는 것만 고친다 — 영문+숫자 조합, ISO 날짜, 수량의 고유어 수사, 식별자,
그리고(상품 카탈로그가 주어지면) 상품명 끝의 품번.

적용 순서가 중요하다: 식별자(주문번호·전화번호·송장번호)를 가장 먼저 처리해야
그 안의 숫자가 뒤따르는 규칙(단위·수량 등)에 다시 걸리지 않는다.
"""
import datetime
import re

# 자릿수 그대로 읽을 숫자
DIGITS = {"0": "공", "1": "일", "2": "이", "3": "삼", "4": "사",
          "5": "오", "6": "육", "7": "칠", "8": "팔", "9": "구"}
# 식별자 접두 알파벳(주문 O·반품 R·고객 C·상품 P)
ID_PREFIX = {"O": "오", "R": "알", "C": "씨", "P": "피"}
# 수량에 쓰는 고유어 수사 (1~10, 20 만 — 주문 수량은 전부 소수라 이 범위로 충분하다)
NATIVE = {1: "한", 2: "두", 3: "세", 4: "네", 5: "다섯", 6: "여섯", 7: "일곱",
          8: "여덟", 9: "아홉", 10: "열", 20: "스무"}
# 영문 약어·단위(긴 것부터 매칭되도록 순서 유지)
UNITS = [("mm", "밀리미터"), ("cm", "센티미터"), ("ml", "밀리리터"), ("kg", "킬로그램")]
ABBREV = [("SPF", "에스피에프"), ("PU", "피유")]
SIZE_LETTERS = [("2XL", "투엑스엘"), ("XL", "엑스엘"), ("S", "에스"), ("M", "엠"), ("L", "엘")]

def _digits_to_ko(s: str) -> str:
    return "".join(DIGITS.get(c, c) for c in s)


_SINO_UNITS = ["", "일", "이", "삼", "사", "오", "육", "칠", "팔", "구"]
_SINO_POS = ["", "십", "백", "천"]


def _sino_number(n: int) -> str:
    """정수를 한자어 수사로 읽는다 (14 → "십사"). 캐럿(14K) 표기 전용 — TTS 가
    숫자 뒤에 알파벳 한 글자가 바로 붙으면(예: "14K") 자릿수 그대로("일사")
    읽어버리는 문제가 있어, 이 경우만 미리 한글 수사로 풀어 쓴다."""
    if n == 0:
        return "영"
    s = str(n)
    length = len(s)
    result = ""
    for i, ch in enumerate(s):
        d = int(ch)
        pos = length - i - 1
        if d == 0:
            continue
        if d == 1 and pos > 0:
            result += _SINO_POS[pos]
        else:
            result += _SINO_UNITS[d] + _SINO_POS[pos]
    return result


def _ids(text: str) -> str:
    """O-1072 · R-2013 · C-0062 · P1001 → 접두 알파벳 + 자릿수 읽기."""
    def repl(m):
        letter, digits = m.group(1), m.group(2)
        return f"{ID_PREFIX[letter]} {_digits_to_ko(digits)}"
    return re.sub(r"\b([ORCP])-?(\d{3,5})\b", repl, text)


def _long_digits(text: str) -> str:
    """전화번호(010-3711-0062)와 10자리 이상 연속 숫자(송장번호)를 자릿수 읽기로."""
    # 전화번호: 하이픈으로 구분된 숫자 그룹 전체(총 9자리 이상)
    def phone_sub(m):
        digits = m.group(0)
        parts = digits.split("-")
        return " ".join(_digits_to_ko(p) for p in parts)

    text = re.sub(r"\b\d{2,3}-\d{3,4}-\d{4}\b", phone_sub, text)
    # 10자리 이상 연속 숫자(송장번호 등)
    text = re.sub(r"\b\d{10,}\b", lambda m: _digits_to_ko(m.group(0)), text)
    return text


def _iso_datetime(text: str, today: datetime.date | None = None) -> str:
    """2026-08-21T12:30:00 → "8월 21일 12시 30분", 2026-08-24 → "8월 24일".
    연도는 올해와 같으면 생략하고, 다르면 "2027년 1월 5일" 처럼 붙인다.
    """
    today = today or datetime.date.today()

    def repl_datetime(m):
        year, month, day, hour, minute = (
            int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5)))
        prefix = f"{year}년 " if year != today.year else ""
        return f"{prefix}{month}월 {day}일 {hour}시 {minute}분"

    def repl_date(m):
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        prefix = f"{year}년 " if year != today.year else ""
        return f"{prefix}{month}월 {day}일"

    text = re.sub(r"\b(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):\d{2}\b", repl_datetime, text)
    text = re.sub(r"\b(\d{4})-(\d{2})-(\d{2})\b", repl_date, text)
    return text


def _alnum_material(text: str) -> str:
    """14K→"십사케이"(숫자를 한자어 수사로 풀어쓰고 K 는 "케이"), 925 실버→"구이오 실버", 80A→"80에이"."""
    # 925 실버 → 구이오 실버 (자릿수 그대로 읽는 소재 표기, "실버" 앞에서만)
    text = re.sub(r"\b925(?=\s*실버)", lambda m: _digits_to_ko(m.group(0)), text)
    # NNK → 한자어 수사+케이 (14K 같은 캐럿 표기. 숫자+영문자 조합은 TTS 가 자릿수
    # 그대로 읽어 "일사케이"가 되므로, 미리 한글 수사로 풀어 쓴다)
    text = re.sub(r"\b(\d+)K\b", lambda m: _sino_number(int(m.group(1))) + "케이", text)
    # 약어(SPF, PU 등)
    for abbr, ko in ABBREV:
        text = re.sub(rf"\b{abbr}\b", ko, text)
    # NN알파벳 (사이즈: 80A → 80에이) — cm/mm/ml/kg 단위는 제외
    text = re.sub(r"\b(\d+)A\b", r"\1에이", text)
    return text


def _units(text: str) -> str:
    for unit, ko in UNITS:
        text = re.sub(rf"(?<=\d){unit}\b", ko, text)
    return text


def _percent(text: str) -> str:
    return re.sub(r"(\d+)%", r"\1 퍼센트", text)


def _size_letters(text: str) -> str:
    """단독으로 쓰인 S·M·L·XL·2XL 을 한글로. 주변에 공백/문장부호가 있는 경우만."""
    for letter, ko in SIZE_LETTERS:
        text = re.sub(rf"(?<![A-Za-z0-9]){re.escape(letter)}(?![A-Za-z0-9])", ko, text)
    return text


def _counts(text: str) -> str:
    """3개 → "세 개". 1~10·20 만 고유어로 바꾸고 그 외는 그대로 둔다."""
    def repl(m):
        n = int(m.group(1))
        if n in NATIVE:
            return f"{NATIVE[n]} 개"
        return m.group(0)
    # 뒤 경계(\b)는 걸지 않는다 — "개" 뒤에 한글 조사("까지" 등)가 바로 붙으면
    # 한글은 \w 로 취급돼 단어 경계가 생기지 않기 때문이다. 대신 앞은 숫자가
    # 아닌 문자 뒤(전체 숫자를 다 집었는지)를 확인해 다자릿수 일부만 집지 않게 한다.
    return re.sub(r"(?<!\d)(\d{1,2})개", repl, text)


def _product_numbers(text: str, product_names: list[str] | None) -> str:
    """상품명 끝 숫자는 수량이 아니라 품번이다. 긴 이름부터 치환해 부분 일치를 막는다."""
    if not product_names:
        return text
    # 중복 제거 후 이름 길이 내림차순
    names = sorted(set(product_names), key=len, reverse=True)
    for name in names:
        if name not in text:
            continue
        m = re.search(r"(\d+)$", name)
        if m:
            spoken_name = name[: m.start()] + m.group(1) + "번"
        else:
            spoken_name = name
        if spoken_name == name:
            continue  # 바뀌는 게 없으면 건너뛴다(멱등성 유지)
        text = text.replace(name, spoken_name)
    return text


def to_speech(text: str, product_names: list[str] | None = None,
              today: datetime.date | None = None) -> str:
    """낭독용 문장으로 변환한다. 화면 표시 문자열(원문)은 건드리지 않는다."""
    if not text:
        return text
    text = _product_numbers(text, product_names)
    text = _ids(text)
    text = _long_digits(text)
    text = _iso_datetime(text, today)
    text = _alnum_material(text)
    text = _units(text)
    text = _percent(text)
    text = _size_letters(text)
    text = _counts(text)
    return text
