// 조회 패널: "현재 고객"(통화 중인 고객의 어드민 상세를 iframe 으로) / "전체 조회"(어드민 홈, 지연 로딩) 탭.
export function createDbPanel() {
  const tabCustomer = document.getElementById("db-tab-customer");
  const tabAll = document.getElementById("db-tab-all");
  const paneCustomer = document.getElementById("db-pane-customer");
  const paneAll = document.getElementById("db-pane-all");
  const emptyEl = document.getElementById("db-customer-empty");
  const customerFrame = document.getElementById("db-customer-frame");
  const allFrame = document.getElementById("db-all-frame");

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

  return {
    // c: 통화 시작 응답의 customer(비회원이면 null). 통화가 끝나도 마지막 값을 유지하므로 초기화 시에는 호출하지 않는다.
    setCustomer(c) {
      if (!c || !c.customer_id) {
        customerFrame.hidden = true;
        emptyEl.hidden = false;
        emptyEl.textContent = "비회원 통화 — 조회할 고객 정보가 없습니다.";
        return;
      }
      emptyEl.hidden = true;
      customerFrame.hidden = false;
      customerFrame.src = `/admin/customers/${encodeURIComponent(c.customer_id)}?embed=1`;
    },
  };
}
