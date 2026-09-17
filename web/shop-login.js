// 로그인 화면 전화번호 입력창에 타이핑하는 대로 하이픈을 넣어준다.
// 순수 함수(formatPhone)로 분리해 두어 브라우저 없이도 테스트할 수 있게 했다.
(function () {
  "use strict";

  // 자유 형식 문자열을 010-1234-5678 형태로 정규화한다.
  // 숫자만 남기고, 국가코드(+82 10... -> 010...)를 보정한 뒤 3-4-4 자리로 하이픈을 넣는다.
  function formatPhone(raw) {
    var digits = String(raw || "").replace(/\D/g, "");
    if (digits.startsWith("82")) {
      digits = "0" + digits.slice(2); // +82 10-1234-5678 붙여넣기 보정
    }
    digits = digits.slice(0, 11); // 최대 11자리(010-1234-5678)

    if (digits.length <= 3) {
      return digits;
    }
    if (digits.length <= 7) {
      return digits.slice(0, 3) + "-" + digits.slice(3);
    }
    return digits.slice(0, 3) + "-" + digits.slice(3, 7) + "-" + digits.slice(7);
  }

  // Node에서 순수 함수 검증에 쓸 수 있도록 노출한다.
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { formatPhone: formatPhone };
  }

  // formatted 문자열에서 앞쪽에 digitCount개의 숫자가 오는 지점의 커서 위치를 구한다.
  function caretForDigitCount(formatted, digitCount) {
    var pos = 0, seen = 0;
    while (pos < formatted.length && seen < digitCount) {
      if (/\d/.test(formatted[pos])) seen++;
      pos++;
    }
    return pos;
  }

  function reformatAndSetCaret(el, rawValue, digitsBeforeCaret) {
    var formatted = formatPhone(rawValue);
    el.value = formatted;
    var pos = caretForDigitCount(formatted, digitsBeforeCaret);
    el.setSelectionRange(pos, pos);
  }

  function init() {
    var input = document.querySelector('input[name="phone"]');
    if (!input) return;

    // 백스페이스는 브라우저 기본 동작에 맡기지 않고 직접 처리한다.
    // 커서가 자동 삽입된 하이픈 바로 뒤에 있을 때, 하이픈만 지우고 나면 재포맷 과정에서
    // 하이픈이 곧바로 되살아나 "지워지지 않는" 것처럼 보이는 흔한 함정이 있다.
    // 그래서 하이픈 앞의 숫자까지 함께 지워서 한 번의 백스페이스로 항상 숫자 하나가 지워지게 한다.
    input.addEventListener("keydown", function (e) {
      if (e.key !== "Backspace") return;
      e.preventDefault();

      var el = e.target;
      var start = el.selectionStart;
      var end = el.selectionEnd;
      var value = el.value;
      var newValue, newCaret;

      if (start !== end) {
        newValue = value.slice(0, start) + value.slice(end);
        newCaret = start;
      } else if (start > 0) {
        var deleteFrom = start - 1;
        if (value[deleteFrom] === "-") deleteFrom -= 1; // 하이픈 앞 숫자까지 같이 삭제
        newValue = value.slice(0, deleteFrom) + value.slice(start);
        newCaret = deleteFrom;
      } else {
        return; // 맨 앞에서는 지울 것이 없다
      }

      var digitsBeforeCaret = newValue.slice(0, newCaret).replace(/\D/g, "").length;
      reformatAndSetCaret(el, newValue, digitsBeforeCaret);
    });

    // 그 외 입력(타이핑, 붙여넣기, Delete 키 등)은 input 이벤트에서 일괄 재포맷한다.
    input.addEventListener("input", function (e) {
      var el = e.target;
      var before = el.value;
      var caret = el.selectionStart == null ? before.length : el.selectionStart;

      // 커서 앞부분에 있는 "숫자 개수"를 세어, 서식이 바뀐 뒤 같은 숫자 개수 위치로 커서를 되돌린다.
      var digitsBeforeCaret = before.slice(0, caret).replace(/\D/g, "").length;
      reformatAndSetCaret(el, before, digitsBeforeCaret);
    });
  }

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", init);
    } else {
      init();
    }
  }
})();
