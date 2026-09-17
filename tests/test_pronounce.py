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


@pytest.mark.parametrize("raw, spoken", [
    # 식별자 뒤에 조사가 바로 붙는 실제 통화 패턴 (한글은 \w 라 \b 가 경계로 안 잡힘)
    ("주문번호 O-1072는 어떻게 되나요", "주문번호 오 일공칠이는 어떻게 되나요"),
    ("010-3711-0062로 연락드립니다", "공일공 삼칠일일 공공육이로 연락드립니다"),
    ("송장번호 621312907791이며 확인됩니다", "송장번호 육이일삼일이구공칠칠구일이며 확인됩니다"),
    ("2026-08-24에 출고됩니다", "8월 24일에 출고됩니다"),
    ("가슴 106cm입니다", "가슴 106센티미터입니다"),
])
def test_rules_fire_before_trailing_korean_particle(raw, spoken):
    """조사가 식별자·단위에 바로 붙는 경우(실제 통화의 기본형)에도 규칙이 발동해야 한다."""
    assert to_speech(raw) == spoken


def test_size_letters():
    assert to_speech("L 사이즈는 가슴 112cm") == "엘 사이즈는 가슴 112센티미터"
    assert to_speech("2XL 까지 있습니다") == "투엑스엘 까지 있습니다"
    assert to_speech("브라 80A 선택 시") == "브라 80에이 선택 시"


def test_size_letters_does_not_touch_after_service_abbreviation():
    """"A/S"(애프터서비스)는 사이즈 표기가 아니므로 건드리지 않는다."""
    assert to_speech("제조사 A/S 관련 문의입니다") == "제조사 A/S 관련 문의입니다"


def test_bra_cup_sizes_b_to_d_are_read():
    assert to_speech("75B 사이즈 있어요?") == "75비 사이즈 있어요?"
    assert to_speech("80C, 85D 순으로 확인해주세요") == "80씨, 85디 순으로 확인해주세요"


def test_month_and_per_unit_are_not_misread_as_count():
    """"3개월"은 "삼 개월"(그대로 두면 TTS 가 맞게 읽음)이지 "세 개월"이 아니다."""
    assert to_speech("보증기간은 3개월입니다") == "보증기간은 3개월입니다"
    assert to_speech("1개당 500원 추가됩니다") == "1개당 500원 추가됩니다"
    assert to_speech("3개 남았습니다") == "세 개 남았습니다"  # 진짜 수량은 그대로 변환


def test_is_idempotent():
    """두 번 돌려도 같은 결과여야 한다 (중복 적용 방지)."""
    once = to_speech("14K 도금 커프 링 3개, 45cm")
    assert to_speech(once) == once


def test_iso_date_year_injected_via_today_param():
    """오늘 날짜에 의존하지 않도록 today 를 주입받는다.
    올해와 같은 연도는 생략하고, 다른 연도는 "N년" 을 붙인다."""
    import datetime
    fixed_today = datetime.date(2026, 9, 17)
    assert to_speech("2026-08-24 출고 예정입니다", today=fixed_today) == "8월 24일 출고 예정입니다"
    assert to_speech("2027-01-05 출고 예정입니다", today=fixed_today) == "2027년 1월 5일 출고 예정입니다"


def test_empty_and_plain_text():
    assert to_speech("") == ""
    assert to_speech("안녕하세요, 모두몰입니다.") == "안녕하세요, 모두몰입니다."


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


def test_product_name_substitution_is_idempotent():
    once = to_speech("14K 도금 커프 링 2 3개 주문하셨습니다", product_names=CATALOG)
    assert to_speech(once, product_names=CATALOG) == once


def test_product_name_substitution_is_idempotent_without_material_prefix():
    """"14K" 가 없는 이름(예: "데님 머플러 2")도 멱등해야 한다.
    이름이 이미 변환된 뒤("데님 머플러 2번")에도 그 이름이 부분 문자열로 남아 있어
    str.replace 를 그대로 쓰면 두 번째 호출에서 "2번번" 이 되는 회귀가 있었다."""
    once = to_speech("데님 머플러 2 3개 주문하셨습니다", product_names=CATALOG)
    assert "머플러 2번" in once
    assert "번번" not in once
    twice = to_speech(once, product_names=CATALOG)
    assert twice == once
    assert "번번" not in twice
