// 관리자 패널: 턴마다 카드 하나. 라우트·확신도·도구·가드레일·소요 시간.
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
      const retryBadge = retried ? `<span class="badge badge-bad">재시도 ${esc(r.attempts)}회</span>` : "";
      const card = document.createElement("article");
      card.className = "turn" + (bad ? " turn-bad" : "");
      card.innerHTML = `
        <div class="q">고객: ${esc(question)}</div>
        <div class="row"><b>라우트</b> ${esc(r.route || "-")} <span class="${low ? "bad" : ""}">conf ${r.confidence == null ? "-" : r.confidence.toFixed(2)}</span>
          ${r.route_alt ? `<span class="muted">2순위 ${esc(r.route_alt)} ${Number(r.alt_confidence || 0).toFixed(2)}</span>` : ""}
          <span class="badge ${bad ? "badge-bad" : ""}">${esc(r.action)}</span>${retryBadge}</div>
        <div class="row"><b>도구</b><ul>${tools}</ul></div>
        <div class="row"><b>가드레일</b> ${guard}</div>
        <div class="row muted">${esc(r.elapsed_ms)} ms</div>
        <div class="a">상담원: ${esc(r.answer)}</div>`;
      root.prepend(card);
    },
  };
}
