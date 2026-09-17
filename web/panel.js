// 관리자 패널: 턴마다 카드 하나. 라우트·확신도·도구·가드레일·근거·소요 시간.
// THRESHOLD(저확신 기준)는 하단 통계 패널(statspanel.js)도 같이 쓰므로 여기 한 곳에서만 정의한다.
import { esc } from "./ordercard.js";

export const THRESHOLD = 0.5;

export function createPanel(root) {
  return {
    clear() {
      root.innerHTML = "";
    },
    addTurn(question, r) {
      const low = r.confidence != null && r.confidence < THRESHOLD;
      const retried = (r.attempts || 0) > 1;
      const bad = ["ESCALATE", "OUT_OF_SCOPE"].includes(r.action) || (r.guardrail && !r.guardrail.ok) || retried;
      const tools = (r.tools || []).map(t => `<li><code>${esc(t.name)}</code> ${esc(JSON.stringify(t.args))}</li>`).join("") || "<li class='muted'>호출 없음</li>";
      const guard = r.guardrail
        ? (r.guardrail.ok ? `<span class="ok">통과</span>` : `<span class="bad">위반</span> ${r.guardrail.violations.map(v => esc(v.type + ": " + v.detail)).join("<br>")}`)
        : `<span class="muted">검사 안 함</span>`;
      // route == null 은 통화 종료 발화 판정으로 즉시 반환된 턴이다 — 매뉴얼을 조회하지 않으므로
      // 근거 줄 자체를 내지 않는다. 그 외(ASK/ESCALATE/OUT_OF_SCOPE 등)는 근거 없음으로 표시한다.
      const evidenceRow = r.route == null ? "" : (() => {
        const ev = r.evidence;
        const routeSecs = ev && ev.route && ev.route.length ? ev.route.map(esc).join(", ") : "";
        const alwaysSecs = ev && ev.always && ev.always.length ? ev.always.map(esc).join(", ") : "";
        const body = (routeSecs || alwaysSecs)
          ? [routeSecs ? `전용 ${routeSecs}` : "", alwaysSecs ? `<span class="muted">공통 ${alwaysSecs}</span>` : ""]
              .filter(Boolean).join(" · ")
          : `<span class="muted">근거 없음</span>`;
        return `<div class="row"><b>근거</b> ${body}</div>`;
      })();
      const retryBadge = retried ? `<span class="badge badge-bad">재시도 ${esc(r.attempts)}회</span>` : "";
      const endCallBadge = r.end_call ? `<span class="badge">통화 종료</span>` : "";
      const card = document.createElement("article");
      card.className = "turn" + (bad ? " turn-bad" : "");
      card.innerHTML = `
        <div class="q">고객: ${esc(question)}</div>
        <div class="row"><b>라우트</b> ${esc(r.route || "-")} <span class="${low ? "bad" : ""}">conf ${r.confidence == null ? "-" : r.confidence.toFixed(2)}</span>
          ${r.route_alt ? `<span class="muted">2순위 ${esc(r.route_alt)} ${Number(r.alt_confidence || 0).toFixed(2)}</span>` : ""}
          <span class="badge ${bad ? "badge-bad" : ""}">${esc(r.action)}</span>${retryBadge}${endCallBadge}</div>
        <div class="row"><b>도구</b><ul>${tools}</ul></div>
        <div class="row"><b>가드레일</b> ${guard}</div>
        ${evidenceRow}
        <div class="row muted">${esc(r.elapsed_ms)} ms</div>
        <div class="a">상담원: ${esc(r.answer)}</div>`;
      root.prepend(card);
    },
  };
}
