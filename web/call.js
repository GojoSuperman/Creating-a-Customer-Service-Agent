// 전화 상태 머신. IDLE → RINGING → SPEAKING ⇄ LISTENING → THINKING → ... → ENDED
import { createVoice } from "./voice.js";
import { createPanel } from "./panel.js";

const $ = (id) => document.getElementById(id);
const state = { phase: "IDLE", callId: null, startedAt: null, turns: 0, textOnly: false, timer: null, gen: 0, busy: false };
const voice = createVoice({ onInterim: (t) => { $("interim").textContent = t; } });
const panel = createPanel($("panel"));

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
  const r = await fetch("/api/call/start", { method: "POST" }).then(r => r.json());
  if (gen !== state.gen) return;
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
  const filler = setTimeout(() => { if (state.phase === "THINKING" && !state.textOnly) voice.speak("잠시만 확인해 드리겠습니다."); }, 1500);
  let r;
  try {
    const res = await fetch("/api/call/turn", { method: "POST", headers: { "Content-Type": "application/json" },
                                                body: JSON.stringify({ call_id: state.callId, text }) });
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
$("voice-select").onchange = (e) => { const v = voice.listVoices()[e.target.value]; if (v) voice.setVoice(v); };

// 초기화
(async () => {
  const d = await fetch("/api/domain").then(r => r.json());
  $("shop-name").textContent = d.name;
  if (d.tts_available) { voice.setMode("server"); $("engine-select").value = "server"; $("voice-select").disabled = true; }
  else { $("engine-select").querySelector('option[value="server"]').disabled = true; }
  if (!voice.supported.recognition) enableTextOnly("이 브라우저는 음성 인식을 지원하지 않습니다. 크롬 또는 엣지를 권장합니다.");
  const fill = () => { $("voice-select").innerHTML = voice.listVoices().map((v, i) => `<option value="${i}">${v.name}</option>`).join(""); };
  fill(); if (window.speechSynthesis) window.speechSynthesis.onvoiceschanged = fill;
  setPhase("IDLE");
})();
