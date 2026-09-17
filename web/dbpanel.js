// 조회 패널: "현재 고객"(상단 = 어드민 고객 상세 iframe, 하단 = 진행 중 주문) / "전체 조회"(어드민 홈, 지연 로딩) 탭.
import { orderCard, activeOrders } from "./ordercard.js";

export function createDbPanel() {
  const tabCustomer = document.getElementById("db-tab-customer");
  const tabAll = document.getElementById("db-tab-all");
  const paneCustomer = document.getElementById("db-pane-customer");
  const paneAll = document.getElementById("db-pane-all");
  const emptyEl = document.getElementById("db-customer-empty");
  const customerFrame = document.getElementById("db-customer-frame");
  const allFrame = document.getElementById("db-all-frame");
  const activeEmptyEl = document.getElementById("db-active-empty");
  const activeListEl = document.getElementById("db-active-orders");

  let allLoaded = false;

  function selectTab(name) {
    const isCustomer = name === "customer";
    tabCustomer.classList.toggle("active", isCustomer);
    tabAll.classList.toggle("active", !isCustomer);
    paneCustomer.hidden = !isCustomer;
    paneAll.hidden = isCustomer;
    if (!isCustomer && !allLoaded) {
      allFrame.src = "/admin/customers";
      allLoaded = true;
    }
  }

  tabCustomer.onclick = () => selectTab("customer");
  tabAll.onclick = () => selectTab("all");

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
        customerFrame.hidden = true;
        emptyEl.hidden = false;
        emptyEl.textContent = "비회원 통화 — 조회할 고객 정보가 없습니다.";
        renderActive(null, null);
        return;
      }
      emptyEl.hidden = true;
      customerFrame.hidden = false;
      customerFrame.src = `/admin/customers/${encodeURIComponent(c.customer_id)}?embed=1`;
      renderActive(c, p);
    },
  };
}
