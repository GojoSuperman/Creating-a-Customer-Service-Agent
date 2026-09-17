// 전화 상태 머신. IDLE → RINGING → SPEAKING ⇄ LISTENING → THINKING → ... → ENDED
import { createVoice, preferredVoice, rememberVoice, voiceKey } from "./voice.js";
import { createPanel } from "./panel.js";
import { createDbPanel } from "./dbpanel.js";
import { createSettings } from "./settings.js";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const state = { phase: "IDLE", callId: null, startedAt: null, turns: 0, textOnly: false, timer: null, gen: 0, busy: false };
const settings = createSettings();
const voice = createVoice({ onInterim: (t) => { $("interim").textContent = t; }, getExtraHeaders: settings.headers });
const panel = createPanel($("panel"));
const dbPanel = createDbPanel();

// 서버가 401(OpenAI 키 없음/무효)을 주면 설정 모달을 열어 안내한다.
function openSettingsForMissingKey() {
  addBubble("system", "OpenAI 키가 필요합니다. 설정에서 키를 넣어 주세요.");
  settings.open("설정에서 OpenAI 키를 입력해 주세요");
}

function setPhase(p) {
  state.phase = p;
  document.body.dataset.phase = p;
  $("status").textContent = { IDLE: "대기", RINGING: "연결 중…", SPEAKING: "상담원 말하는 중", LISTENING: "듣는 중 — 말씀하세요",
                              THINKING: "확인 중…", ENDED: "통화 종료" }[p];
}

function playRing(ms) {
  return new Promise((resolve) => {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator(); const gain = ctx.createGain();
    osc.type = "sine"; osc.frequency.value = 440; gain.gain.value = 0.08;
    osc.connect(gain).connect(ctx.destination); osc.start();
    let on = true;
    const iv = setInterval(() => { on = !on; gain.gain.value = on ? 0.08 : 0; }, 400);
    setTimeout(() => { clearInterval(iv); osc.stop(); ctx.close(); resolve(); }, ms);
  });
}

function addBubble(who, text) {
  const div = document.createElement("div");
  div.className = "bubble " + who;
  div.textContent = text;
  $("transcript").appendChild(div);
  $("transcript").scrollTop = $("transcript").scrollHeight;
}

async function startCall() {
  const gen = ++state.gen;
  panel.clear(); $("transcript").innerHTML = ""; state.turns = 0;
  setPhase("RINGING");
  await playRing(1500);
  if (gen !== state.gen) return;
  const sel = $("sample-select").value;
  const phone = (sel === "__manual" ? $("phone-input").value : sel) || null;
  const r = await fetch("/api/call/start", { method: "POST",
                                             headers: { "Content-Type": "application/json", ...settings.headers() },
                                             body: JSON.stringify({ phone }) }).then(r => r.json());
  if (gen !== state.gen) return;
  panel.setCustomer(r.customer || null, r.profile || null);
  dbPanel.setCustomer(r.customer || null);
  state.callId = r.call_id; state.startedAt = Date.now();
  clearInterval(state.timer);
  state.timer = setInterval(() => {
    const s = Math.floor((Date.now() - state.startedAt) / 1000);
    $("clock").textContent = `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  }, 500);
  await say(r.greeting);
  listenLoop();
}

async function say(text) {
  setPhase("SPEAKING");
  addBubble("agent", text);
  if (!state.textOnly) await voice.speak(text);
}

async function listenLoop() {
  if (state.phase === "ENDED") return;
  if (state.textOnly) { setPhase("LISTENING"); return; }   // 텍스트 입력을 기다린다
  setPhase("LISTENING");
  const gen = state.gen;
  let text = "";
  try { text = await voice.listen(); }
  catch (e) {
    if (gen !== state.gen || state.phase !== "LISTENING") return;
    if (e.message === "not-allowed" || e.message === "unsupported") { enableTextOnly("마이크를 쓸 수 없어 텍스트 입력으로 전환했습니다."); return; }
    // no-speech 등은 다시 듣는다
    return listenLoop();
  }
  if (gen !== state.gen || state.phase !== "LISTENING") return;
  $("interim").textContent = "";
  if (!text) return listenLoop();
  await sendTurn(text);
}

async function sendTurn(text) {
  if (state.busy || state.phase === "ENDED" || !state.callId) return;
  state.busy = true;
  addBubble("customer", text);
  setPhase("THINKING");
  const gen = state.gen;
  // 응답이 1.5초 넘게 걸리면 안내 음성을 먼저 낸다. 답변이 도착해도 이 문장이 끝난 뒤에 읽는다
  // (바로 끊으면 "잠시만 기…" 처럼 중간에 잘린다).
  let fillerDone = Promise.resolve();
  const filler = setTimeout(() => {
    if (state.phase === "THINKING" && !state.textOnly) fillerDone = voice.speak("잠시만 확인해 드리겠습니다.");
  }, 1500);
  let r;
  try {
    const res = await fetch("/api/call/turn", {
      method: "POST", headers: { "Content-Type": "application/json", ...settings.headers() },
      body: JSON.stringify({ call_id: state.callId, text }),
    });
    if (res.status === 401) {
      clearTimeout(filler);
      if (gen !== state.gen || state.phase === "ENDED") { state.busy = false; return; }
      openSettingsForMissingKey();
      state.busy = false;
      return listenLoop();
    }
    if (!res.ok) throw new Error(`서버 오류 ${res.status}: ${await res.text()}`);
    r = await res.json();
  } catch (e) {
    clearTimeout(filler);
    if (gen !== state.gen || state.phase === "ENDED") { state.busy = false; return; }
    addBubble("system", String(e.message));
    state.busy = false;
    return listenLoop();
  }
  clearTimeout(filler);
  if (gen !== state.gen || state.phase === "ENDED") { state.busy = false; return; }
  state.turns += 1;
  panel.addTurn(text, r);
  await fillerDone;                       // 안내 음성이 재생 중이면 끝날 때까지 기다린다
  if (gen !== state.gen || state.phase === "ENDED") { state.busy = false; return; }
  await say(r.answer);
  if (gen !== state.gen || state.phase === "ENDED") { state.busy = false; return; }
  if (r.end_call) { state.busy = false; return endCall("에이전트가 통화를 종료했습니다"); }
  state.busy = false;
  listenLoop();
}

function endCall(reason = "통화를 끊었습니다") {
  state.gen += 1;
  state.busy = false;
  voice.stop();
  clearInterval(state.timer);
  setPhase("ENDED");
  addBubble("system", `${reason} · ${$("clock").textContent} · ${state.turns}턴`);
  if (state.callId) {
    fetch("/api/call/end", { method: "POST", headers: { "Content-Type": "application/json" },
                             body: JSON.stringify({ call_id: state.callId }) }).catch(() => {});
  }
  state.callId = null;
}

function enableTextOnly(msg) {
  state.textOnly = true;
  document.body.classList.add("text-only");
  if (msg) addBubble("system", msg);
  setPhase("LISTENING");
}

// ── 이벤트 ──
$("btn-call").onclick = () => { if (state.phase === "IDLE" || state.phase === "ENDED") startCall(); };
$("btn-hangup").onclick = () => { if (state.phase !== "IDLE" && state.phase !== "ENDED") endCall(); };
$("text-form").onsubmit = (e) => {
  e.preventDefault();
  const t = $("text-input").value.trim();
  if (!t || !state.callId) return;
  if (!(state.phase === "LISTENING" && !state.busy)) return;
  $("text-input").value = "";
  voice.stop();
  sendTurn(t);
};
$("engine-select").onchange = (e) => { voice.setMode(e.target.value); $("voice-select").disabled = e.target.value === "server"; };
$("voice-select").onchange = (e) => {
  const v = voice.listVoices().find(v => voiceKey(v) === e.target.value);
  if (v) { voice.setVoice(v); rememberVoice(v); }
};

// 초기화
(async () => {
  const d = await fetch("/api/domain").then(r => r.json());
  $("shop-name").textContent = d.name;
  // 전화 걸기 옆 고객 드롭다운: 미리 준비된 고객 이름을 골라 그 발신 번호로 테스트한다
  $("sample-select").innerHTML = '<option value="">비회원(번호 없음)</option>' +
    (d.sample_customers || []).map(c => `<option value="${esc(c.phone)}">${esc(c.name)}${c.hint ? " · " + esc(c.hint) : ""}</option>`).join("") +
    '<option value="__manual">번호 직접 입력…</option>';
  $("sample-select").onchange = (e) => {
    const manual = e.target.value === "__manual";
    $("phone-input").hidden = !manual;
    if (manual) $("phone-input").focus();
  };
  // 기본은 무료인 브라우저 음성. 서버 TTS 가 설정된 경우에만 OpenAI 를 고를 수 있다
  if (!d.tts_available) $("engine-select").querySelector('option[value="server"]').disabled = true;
  if (!voice.supported.recognition) enableTextOnly("이 브라우저는 음성 인식을 지원하지 않습니다. 크롬 또는 엣지를 권장합니다.");
  const fill = () => {
    const vs = voice.listVoices();
    let pref = preferredVoice(vs);
    if (!pref && vs.length) pref = vs[0];   // 저장된 선호 음성이 걸러진 목록에 없으면 첫 번째(로컬)로 대체
    if (pref) { voice.setVoice(pref); rememberVoice(pref); }
    // 표시 이름 정리: 크롬 내장 음성 "Google 한국의" 는 "Google" 로 보여 준다 (음성은 동일)
    const label = (v) => /^google/i.test(v.name) ? "Google" : v.name;
    $("voice-select").innerHTML = vs.map((v) =>
      `<option value="${esc(voiceKey(v))}" ${v === pref ? "selected" : ""}>${esc(label(v))}</option>`).join("");
    // 거르지 못하고 원격 음성이 그대로 남아 있으면(예: 엣지에서 로컬 음성이 없는 경우) 안내 문구를 붙인다
    const ua = navigator.userAgent;
    const isChrome = /Edg\//.test(ua) ? false : /Chrome\//.test(ua);
    $("voice-select").title = (!isChrome && vs.some(v => v.localService === false))
      ? "일부 음성은 소리가 나지 않을 수 있습니다" : "";
  };
  fill(); if (window.speechSynthesis) window.speechSynthesis.onvoiceschanged = fill;
  setPhase("IDLE");
})();
