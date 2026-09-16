from server.domain import load_domain
from server.context import split_sections, build_context

SAMPLE = """# 제목
머리말입니다.

## 이 매뉴얼을 쓰는 방법
규약.

## 0. 원칙
원칙 본문.

## 4. 배송 문의
### 4.1 배송비
기본 배송비 2,500원.

## 6. 반품 비용
반품 배송비 5,000원.

## 부록 A. 요약
부록 본문.
"""


def test_split_sections_keys_and_appendix_excluded():
    s = split_sections(SAMPLE)
    assert s["_header"] == "# 제목\n머리말입니다."
    assert set(s) == {"_header", "이 매뉴얼을 쓰는 방법", "0", "4", "6"}
    assert s["4"].startswith("## 4. 배송 문의")
    assert "4.1 배송비" in s["4"]


def test_shipping_context_excludes_return_sections(modumall_dir):
    d = load_domain(modumall_dir)
    ctx = build_context(d, "SHIPPING")
    assert "## 4. 배송 문의" in ctx
    assert "## 0. 상담 기본 원칙" in ctx
    assert "## 7. 응대 범위와 이관" in ctx
    assert "## 5. 교환·반품·환불" not in ctx
    assert "## 6. 교환·반품·환불" not in ctx
    assert "## 부록" not in ctx


def test_return_context_has_both_chapters(modumall_dir):
    d = load_domain(modumall_dir)
    ctx = build_context(d, "RETURN_REFUND")
    assert "## 5. 교환·반품·환불" in ctx
    assert "## 6. 교환·반품·환불" in ctx
    assert "## 4. 배송 문의" not in ctx


def test_other_context_is_only_always_sections(modumall_dir):
    d = load_domain(modumall_dir)
    ctx = build_context(d, "OTHER")
    assert "## 7. 응대 범위와 이관" in ctx
    assert "## 2. 주문" not in ctx
