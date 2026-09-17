// 조회 패널: "현재 고객" 단일 패널. 상단 = 고객 기본정보 + 최근 주문, 하단 = 진행 중 주문.
// 예전에는 상단이 어드민 고객 상세 iframe이었으나, ADMIN_PASSWORD 미설정/Basic 인증 문제로
// 화면에 고객 정보가 안 보이는 문제가 있어 통화 데이터로 직접 렌더링하도록 바꿨다.
import { esc, orderCard, activeOrders } from "./ordercard.js";

export function createDbPanel() {
  const emptyEl = document.getElementById("db-customer-empty");
  const cardEl = document.getElementById("db-customer-card");
  const activeEmptyEl = document.getElementById("db-active-empty");
  const activeListEl = document.getElementById("db-active-orders");

  function renderCustomerInfo(c, p) {
    if (!c) {
      emptyEl.hidden = false;
      emptyEl.textContent = "비회원 통화 — 조회할 고객 정보가 없습니다.";
      cardEl.hidden = true;
      cardEl.innerHTML = "";
      return;
    }
    emptyEl.hidden = true;
    cardEl.hidden = false;
    const prof = p || {};
    const orders = prof.orders || ((c.recent_orders || []).map(o => ({ ...o, in_progress: false })));
    const rest = orders.filter(o => !o.in_progress);
    const info = `<dl>
      <dt>전화</dt><dd>${esc(prof.phone || c.phone || "-")}</dd>
      <dt>주소</dt><dd>${esc(prof.address || "-")}${prof.address_region ? ` <span class="muted">(${esc(prof.address_region)})</span>` : ""}</dd>
      ${prof.joined_at ? `<dt>가입</dt><dd>${esc(prof.joined_at)}</dd>` : ""}
    </dl>`;
    const restHtml = `<h4>최근 주문</h4><ul>${rest.map(orderCard).join("") || "<li class='muted'>완료된 최근 주문 없음</li>"}</ul>`;
    cardEl.innerHTML = `<div class="customer-card"><div class="name">${esc(c.name)} 고객님<small>${esc(c.customer_id || "")}</small></div>${info}${restHtml}</div>`;
  }

  function renderActive(c, p) {
    activeEmptyEl.hidden = true;
    activeListEl.hidden = false;
    if (!c) {
      activeListEl.innerHTML = `<div class="customer-card guest">비회원 통화입니다. 진행 중 주문 정보가 없습니다.</div>`;
      return;
    }
    const active = activeOrders(c, p);
    activeListEl.innerHTML = `<div class="customer-card">
      <h4>진행 중 주문 ${active.length}건</h4>
      <ul>${active.map(orderCard).join("") || "<li class='muted'>진행 중인 주문 없음</li>"}</ul>
    </div>`;
  }

  return {
    // c: 통화 시작 응답의 customer(비회원이면 null). p: 상담원 전용 프로필(진행 중 주문 상세 포함).
    // 통화가 끝나도 마지막 값을 유지하므로 초기화 시에는 호출하지 않는다.
    setCustomer(c, p) {
      if (!c || !c.customer_id) {
        renderCustomerInfo(null, null);
        renderActive(null, null);
        return;
      }
      renderCustomerInfo(c, p);
      renderActive(c, p);
    },
  };
}
