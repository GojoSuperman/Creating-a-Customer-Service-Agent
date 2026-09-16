// 관리자 패널: 턴마다 카드 하나. 라우트·확신도·도구·가드레일·소요 시간.
export function createPanel(root) {
  const THRESHOLD = 0.5;
  function esc(s) { return String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

  function orderLine(o) {
    const d = o.ordered_at ? `${parseInt(o.ordered_at.slice(5, 7), 10)}월 ${parseInt(o.ordered_at.slice(8, 10), 10)}일` : "-";
    const amount = o.order_amount != null ? `${Number(o.order_amount).toLocaleString()}원` : "-";
    return `${esc(o.order_id)} · ${esc(d)} · ${esc(o.status || "-")} · ${esc(o.items_summary || "")} · ${esc(amount)}`;
  }

  return {
    clear() {
      const card = document.getElementById("customer-card");
      root.innerHTML = "";
      if (card) root.appendChild(card);
    },
    setCustomer(c) {
      const old = document.getElementById("customer-card");
      if (old) old.remove();
      if (!c) return;
      const card = document.createElement("article");
      card.id = "customer-card";
      card.className = "customer-card";
      const orders = (c.recent_orders || []).slice(0, 3).map(o => `<li>${orderLine(o)}</li>`).join("") || "<li class='muted'>최근 주문 없음</li>";
      card.innerHTML = `<div class="name">${esc(c.name)}</div><ul>${orders}</ul>`;
      root.prepend(card);
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
          <span class="badge ${bad ? "badge-bad" : ""}">${esc(r.action)}</span>${retryBadge}</div>
        <div class="row"><b>도구</b><ul>${tools}</ul></div>
        <div class="row"><b>가드레일</b> ${guard}</div>
        <div class="row muted">${esc(r.elapsed_ms)} ms</div>
        <div class="a">상담원: ${esc(r.answer)}</div>`;
      root.prepend(card);
    },
  };
}
