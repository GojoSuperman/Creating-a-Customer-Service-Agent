// 주문 카드 렌더링(진행 중 주문의 상태·택배·배송이력 등 상세 포함). 관리자 패널(panel.js)의 턴 카드와
// 우측 조회 패널(dbpanel.js)의 "진행 중 주문" 영역이 똑같은 렌더링을 공유하도록 한 곳에서만 정의한다.
export function esc(s) { return String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
export function mmdd(iso) { return iso ? `${parseInt(iso.slice(5, 7), 10)}월 ${parseInt(iso.slice(8, 10), 10)}일` : "-"; }
export function won(n) { return n == null ? "-" : `${Number(n).toLocaleString()}원`; }

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

export function orderCard(o) {
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

// 진행 중 주문 목록: 고객 프로필(p)이 있으면 그 orders, 없으면 최근 주문(c.recent_orders)을 모두
// "진행 중 아님" 취급해 반환한다(panel.js setCustomer가 쓰던 것과 동일한 폴백 규칙).
export function activeOrders(c, p) {
  const prof = p || {};
  const orders = prof.orders || ((c && c.recent_orders) || []).map(o => ({ ...o, in_progress: false }));
  return orders.filter(o => o.in_progress);
}
