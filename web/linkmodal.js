// 헤더의 "쇼핑몰" 링크를 새 탭 대신 모달(팝업)로 띄운다.
// 설정 모달(settings.js)과 같은 <dialog> 패턴: Esc·배경 클릭·X 버튼으로 닫힌다.
export function createLinkModal() {
  const dialog = document.getElementById("link-modal");
  const title = document.getElementById("link-modal-title");
  const frame = document.getElementById("link-modal-frame");
  const btnClose = document.getElementById("link-modal-close");

  let loadedHref = "";

  function open(href, label) {
    title.textContent = label || "";
    frame.title = label || "링크 미리보기";
    // 지연 로딩: 처음 열 때만 iframe 을 로드한다.
    // 같은 링크를 다시 열 때는 새로 불러오지 않고 이전 상태(스크롤 위치 등)를 유지한다.
    // 다른 href 로 열 때만 src 를 바꿔 새로 로드한다.
    if (loadedHref !== href) {
      frame.src = href;
      loadedHref = href;
    }
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  }

  function close() {
    dialog.close();
  }

  btnClose.onclick = close;

  // 배경(백드롭) 클릭으로 닫기: <dialog> 는 열려 있을 때 자신의 영역이 뷰포트 전체이므로,
  // 실제 콘텐츠(link-modal-head/frame) 밖을 클릭하면 이벤트 target 이 dialog 자신이 된다.
  dialog.addEventListener("click", (e) => {
    if (e.target === dialog) close();
  });

  document.querySelectorAll("[data-modal-href]").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.preventDefault();
      open(el.getAttribute("data-modal-href"), el.getAttribute("data-modal-title") || el.textContent);
    });
  });

  return { open, close };
}
