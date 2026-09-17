// 통화 화면 설정 모달: 사용자가 자기 OpenAI 키를 넣는다.
// 키는 이 브라우저의 localStorage 에만 저장하고, 서버로는 요청마다 X-OpenAI-Key 헤더로만 보낸다.
// (서버는 그 요청 동안만 쓰고 저장하지 않는다 — server/app.py, server/llmkey.py 참고)
const STORAGE_KEY = "modumall.openai_key";

function maskKey(key) {
  if (!key) return "";
  const tail = key.slice(-4);
  return `sk-…${tail}`;
}

export function getStoredKey() {
  try { return localStorage.getItem(STORAGE_KEY) || ""; }
  catch (_) { return ""; }
}

function setStoredKey(key) {
  try {
    if (key) localStorage.setItem(STORAGE_KEY, key);
    else localStorage.removeItem(STORAGE_KEY);
  } catch (_) { /* 저장소를 쓸 수 없는 환경(프라이빗 모드 등)이면 조용히 무시한다 */ }
}

export function keyHeaders() {
  const key = getStoredKey();
  return key ? { "X-OpenAI-Key": key } : {};
}

export function createSettings() {
  const dialog = document.getElementById("settings-modal");
  const input = document.getElementById("settings-key-input");
  const status = document.getElementById("settings-key-status");
  const btnOpen = document.getElementById("btn-settings");
  const btnCheck = document.getElementById("btn-key-check");
  const btnClear = document.getElementById("btn-key-clear");
  const form = dialog.querySelector("form");

  function setStatus(text, kind) {
    status.textContent = text;
    status.className = "settings-key-status" + (kind ? ` ${kind}` : "");
  }

  function refreshView() {
    const key = getStoredKey();
    input.value = "";
    input.placeholder = key ? maskKey(key) : "sk-...";
    setStatus(key ? `저장된 키가 있습니다 (${maskKey(key)}).` : "저장된 키가 없습니다.");
  }

  function open(message) {
    refreshView();
    if (message) setStatus(message, "bad");
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
    input.focus();
  }

  btnOpen.onclick = () => open();

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const v = input.value.trim();
    if (v) setStoredKey(v);
    refreshView();
    dialog.close();
  });

  btnClear.onclick = () => {
    setStoredKey("");
    input.value = "";
    refreshView();
  };

  btnCheck.onclick = async () => {
    const key = input.value.trim() || getStoredKey();
    if (!key) { setStatus("키를 먼저 입력하세요.", "bad"); return; }
    setStatus("확인 중…");
    try {
      const res = await fetch("/api/key/check", { method: "POST", headers: { "X-OpenAI-Key": key } });
      if (res.status === 401) { setStatus("키를 먼저 입력하세요.", "bad"); return; }
      const data = await res.json();
      setStatus(data.message || (data.ok ? "사용할 수 있는 키입니다" : "키가 올바르지 않습니다"),
                data.ok ? "ok" : "bad");
    } catch (_) {
      setStatus("확인할 수 없습니다. 네트워크를 확인하세요.", "bad");
    }
  };

  return {
    open,
    headers: keyHeaders,
    hasKey: () => !!getStoredKey(),
  };
}
