// 관리자 패널: 턴마다 카드 하나. 라우트·확신도·도구·가드레일·소요 시간.
// THRESHOLD(저확신 기준)는 하단 통계 패널(statspanel.js)도 같이 쓰므로 여기 한 곳에서만 정의한다.
export const THRESHOLD = 0.5;

export function createPanel(root) {
  function esc(s) { return String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

  function mmdd(iso) { return iso ? `${parseInt(iso.slice(5, 7), 10)}월 ${parseInt(iso.slice(8, 10), 10)}일` : "-"; }
  function won(n) { return n == null ? "-" : `${Number(n).toLocaleString()}원`; }
  function statusClass(st) {
    if (!st) return "";
    if (st === "배송완료") return "done";
    if (st === "배송중") return "ship";
    if (st === "결제완료") return "wait";
    if (st === "제작중") return "make";
    if (st === "배송지연") return "delay";
    if (st.includes("반품") || st.includes("교환")) return "ret";
    return "";
  }
  function orderCard(o) {
    const head = `<div class="head"><span class="id">${esc(o.order_id)}</span><span>${esc(mmdd(o.ordered_at))}</span>
      <span class="status ${statusClass(o.status)}">${esc(o.status || "-")}</span><span class="amt">${esc(won(o.order_amount))}</span></div>`;
    const items = `<div class="items">${esc(o.items_summary || "")}</div>`;
    if (!o.in_progress) return `<li class="order">${head}${items}</li>`;
    const d = [];
    if (o.status_detail) d.push(esc(o.status_detail));
    if (o.courier || o.tracking_no) d.push(`${esc(o.courier || "")} ${esc(o.tracking_no || "")}`.trim());
    if (o.expected_ship_date && !o.shipped_at) d.push(`출고 예정 ${esc(mmdd(o.expected_ship_date))}`);
    if (o.shipped_at) d.push(`출고 ${esc(mmdd(o.shipped_at))}`);
    if (o.expected_delivery && !o.delivered_at) d.push(`도착 예정 ${esc(mmdd(o.expected_delivery))}`);
    if (o.delay_days) d.push(`<span class="bad">지연 ${esc(o.delay_days)}일${o.delay_reason ? " · " + esc(o.delay_reason) : ""}</span>`);
    if (o.return_) {
      const r = o.return_;
      d.push(`${esc(r.type || "반품")} ${esc(r.return_id)} · 단계: ${esc(r.stage || "-")}` +
        (r.expected_completion ? ` · 완료 예정 ${esc(mmdd(r.expected_completion))}` : "") +
        (r.refund_amount != null ? ` · 환불 ${esc(won(r.refund_amount))}` : ""));
    }
    const events = (o.events || []).length
      ? `<ul class="events">${o.events.map(e => `<li>${esc(mmdd(e.at))} ${esc(e.status)}${e.location ? " · " + esc(e.location) : ""}</li>`).join("")}</ul>`
      : "";
    return `<li class="order">${head}${items}<div class="detail">${d.join("<br>")}</div>${events}</li>`;
  }

  return {
    clear() {
      const card = document.getElementById("customer-card");
      root.innerHTML = "";
      if (card) root.appendChild(card);
    },
    // c: 프롬프트용 고객(이름·최근 주문), p: 상담원 전용 프로필(주소·전화·진행 중 주문 상세). 둘 다 없으면 비회원.
    setCustomer(c, p) {
      const old = document.getElementById("customer-card");
      if (old) old.remove();
      const card = document.createElement("article");
      card.id = "customer-card";
      if (!c) {
        card.className = "customer-card guest";
        card.innerHTML = `<div class="name">비회원 통화</div><div>발신 번호로 확인된 고객이 없습니다. 주문번호를 물어봐야 합니다.</div>`;
        root.prepend(card);
        return;
      }
      card.className = "customer-card";
      const prof = p || {};
      const orders = prof.orders || (c.recent_orders || []).map(o => ({ ...o, in_progress: false }));
      const active = orders.filter(o => o.in_progress);
      const rest = orders.filter(o => !o.in_progress);
      const info = `<dl>
        <dt>전화</dt><dd>${esc(prof.phone || c.phone || "-")}</dd>
        <dt>주소</dt><dd>${esc(prof.address || "-")}${prof.address_region ? ` <span class="muted">(${esc(prof.address_region)})</span>` : ""}</dd>
        ${prof.joined_at ? `<dt>가입</dt><dd>${esc(prof.joined_at)}</dd>` : ""}
      </dl>`;
      const activeHtml = `<h4>진행 중 주문 ${active.length}건</h4><ul>${active.map(orderCard).join("") || "<li class='muted'>진행 중인 주문 없음</li>"}</ul>`;
      const restHtml = `<h4>최근 주문</h4><ul>${rest.map(orderCard).join("") || "<li class='muted'>완료된 최근 주문 없음</li>"}</ul>`;
      card.innerHTML = `<div class="name">${esc(c.name)} 고객님<small>${esc(c.customer_id || "")}</small></div>${info}${activeHtml}${restHtml}`;
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
