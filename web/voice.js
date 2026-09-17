// 음성 인식·합성 모듈. listen / speak / stop 과 supported / listVoices / setVoice 를 노출한다.
// Web Speech API (크롬·엣지). 서버 TTS로 바꿀 때는 이 파일만 교체한다.
// 화면에는 "O-1001" 그대로 보여 주되, 읽을 때는 "오 다시 일 공 공 일"처럼 한 자리씩 한국어로 읽는다.
// TTS 가 알파벳 O 를 영어로 발음하는 것을 막고, 전화에서 숫자를 또박또박 전달하기 위함이다.
// 기본 목소리 선호 순서: 사용자가 마지막에 고른 것 > 크롬의 "Google 한국의" > 엣지의 Natural/Online > 아무 한국어
const VOICE_PREF_KEY = "modumall.voice";
export function preferredVoice(koVoices) {
  let saved = null;
  try { saved = localStorage.getItem(VOICE_PREF_KEY); } catch (_) {}
  return (saved && koVoices.find(v => v.name === saved))
      || koVoices.find(v => /google/i.test(v.name))
      || koVoices.find(v => /natural|online/i.test(v.name))
      || null;
}
export function rememberVoice(v) { try { localStorage.setItem(VOICE_PREF_KEY, v.name); } catch (_) {} }

// 음성 목록 안정 식별자: voiceURI 가 없으면 name+lang 으로 대체한다 (배열 인덱스는 목록이
// 바뀌면 어긋나므로 쓰지 않는다).
export function voiceKey(v) { return v.voiceURI || `${v.name}|${v.lang}`; }

// 한국어 음성만 추리고, 완전히 같은 음성(voiceURI 동일)만 하나로 합친다.
// 주의: 어떤 음성이 실제로 소리를 내는지는 브라우저·OS·설치 상태에 따라 달라 UA 나
// localService 값만으로는 추측할 수 없다(엣지에서도 원격 음성이 재생되는 사례가 실측으로
// 확인됨). 그래서 여기서는 "될 것 같은 것"을 걸러내지 않고, 중복만 정리한다.
// 실제로 소리가 나는지는 미리듣기 버튼으로 사용자가 직접 확인한다.
// 예전에는 name+lang 이 같으면 같은 음성으로 보고 지웠는데, 이름이 같아도(예: 여러 "Google"
// 음성) 실제로는 서로 다른 음성일 수 있어 실측 결과 소리가 나는 음성이 사라지는 문제가
// 있었다. voiceURI 가 완전히 같은 경우만 같은 음성으로 본다(브라우저가 같은 음성을 중복
// 노출하는 경우만 제거). voiceURI 가 없는 브라우저는 name+lang+localService 로 대체한다.
export function dedupeVoices(voices) {
  const ko = (voices || []).filter(v => v.lang && v.lang.toLowerCase().startsWith("ko"));
  const seen = new Set();
  const deduped = [];
  for (const v of ko) {
    const key = v.voiceURI || `${v.name}|${v.lang}|${v.localService}`;
    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push(v);
  }
  return deduped;
}

// 목록에 표시할 이름을 구분되게 만든다. 이름이 유일하면 이름만 그대로 보여 주고,
// 같은 이름이 여러 개면 언어 태그·로컬/온라인 구분을 붙이고, 그래도 같으면 순번을 붙인다.
// voices 와 같은 길이·순서의 라벨 배열을 반환한다 (voices[i] 의 라벨은 labelVoices(voices)[i]).
// 브라우저가 붙인 음성 이름 자체가 어색한 경우를 바로잡는 표시용 치환 표.
// 음성 식별(voiceURI)에는 쓰지 않고, 화면에 보여 줄 이름에만 적용한다.
// 표에 없는 이름은 추측하지 않고 그대로 둔다.
const VOICE_NAME_FIXES = {
  "Google 한국의": "Google 한국어",
};
function displayName(name) { return VOICE_NAME_FIXES[name] || name; }

export function labelVoices(voices) {
  const list = voices || [];
  const byName = new Map();
  for (const v of list) {
    const dn = displayName(v.name);
    const arr = byName.get(dn) || [];
    arr.push(v);
    byName.set(dn, arr);
  }
  const labelByKey = new Map();
  for (const group of byName.values()) {
    if (group.length === 1) {
      labelByKey.set(voiceKey(group[0]), displayName(group[0].name));
      continue;
    }
    const bases = group.map((v) => {
      const svc = v.localService ? "로컬" : "온라인";
      const dn = displayName(v.name);
      return v.lang ? `${dn} (${svc} · ${v.lang})` : `${dn} (${svc})`;
    });
    const baseCount = new Map();
    for (const b of bases) baseCount.set(b, (baseCount.get(b) || 0) + 1);
    const seenIdx = new Map();
    group.forEach((v, i) => {
      const base = bases[i];
      if (baseCount.get(base) > 1) {
        const idx = (seenIdx.get(base) || 0) + 1;
        seenIdx.set(base, idx);
        labelByKey.set(voiceKey(v), `${base} ${idx}`);
      } else {
        labelByKey.set(voiceKey(v), base);
      }
    });
  }
  return list.map((v) => labelByKey.get(voiceKey(v)));
}

const KO_DIGITS = "공일이삼사오육칠팔구";
const LETTER_KO = { O: "오", R: "알", P: "피" };
export function speakable(text) {
  return String(text).replace(/\b([ORP])(-?)(\d{3,4})\b/g, (m, letter, dash, digits) => {
    const spoken = [...digits].map((c) => KO_DIGITS[+c]).join(" ");
    return dash ? `${LETTER_KO[letter]} 다시 ${spoken}` : `${LETTER_KO[letter]} ${spoken}`;
  });
}

// TTS 재생 전 선행 무음 길이(ms). 절전 상태로 들어간 출력 장치(특히 블루투스 스피커)가
// 오디오 스트림을 받고 깨어나는 데 걸리는 시간을 벌어, 말소리 첫 음절이 잘리지 않게 한다.
// 너무 길면 응답이 굼떠 보이므로 필요한 만큼만 짧게 잡는다. 사용자가 들어보고 조절 가능.
const LEAD_SILENCE_MS = 350;
// 완전한 디지털 0 무음은 일부 OS·블루투스 스택이 "재생할 소리 없음"으로 판단해 절전 중인
// 장치를 깨우지 않는 경우가 있다. 그래서 사람 귀에는 들리지 않는 수준의 아주 작은 진폭을
// 채워 "소리가 나고 있다"고 인식시킨다.
const LEAD_SILENCE_GAIN = 0.0005;

// AudioContext 는 브라우저별로 생성 개수에 제한이 있어 모듈 전체에서 하나만 만들어 재사용한다.
let sharedAudioCtx = null;
function getAudioCtx() {
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) return null;
  if (!sharedAudioCtx) {
    try { sharedAudioCtx = new Ctx(); } catch (_) { return null; }
  }
  return sharedAudioCtx;
}

// 선행 무음을 재생한다. { promise, cancel } 을 반환하며 promise 는 무음 재생이 끝나거나
// (AudioContext 부재·resume 실패 등으로) 건너뛰거나 cancel() 로 중단되면 resolve 된다.
// 실패해도 절대 reject 하지 않는다 — 무음 재생 실패가 TTS 자체를 막아서는 안 되기 때문이다.
function playLeadSilence() {
  const ctx = getAudioCtx();
  if (!ctx) return { promise: Promise.resolve(), cancel: () => {} };
  let cancelled = false;
  let source = null;
  const run = async () => {
    if (ctx.state === "suspended") {
      // 사용자 제스처 전에는 브라우저 자동재생 정책으로 suspended 일 수 있다.
      // resume 이 실패하면 무음을 포기하고 조용히 말소리로 넘어간다.
      try { await ctx.resume(); } catch (_) { return; }
    }
    if (cancelled || ctx.state !== "running") return;
    await new Promise((resolve) => {
      const frames = Math.max(1, Math.round(ctx.sampleRate * (LEAD_SILENCE_MS / 1000)));
      const buffer = ctx.createBuffer(1, frames, ctx.sampleRate);
      const data = buffer.getChannelData(0);
      for (let i = 0; i < data.length; i++) data[i] = LEAD_SILENCE_GAIN;
      source = ctx.createBufferSource();
      source.buffer = buffer;
      source.connect(ctx.destination);
      source.onended = resolve;
      if (cancelled) { try { source.stop(); } catch (_) {} return; }
      source.start();
    });
  };
  const promise = run().catch(() => {});
  return {
    promise,
    cancel: () => {
      cancelled = true;
      if (source) { try { source.stop(); } catch (_) {} }
    },
  };
}

export function createVoice({ lang = "ko-KR", onInterim = () => {}, getExtraHeaders = () => ({}) } = {}) {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const synth = window.speechSynthesis;
  let rec = null;
  let currentUtter = null;
  let voice = null;
  let mode = "browser";          // "browser" | "server"
  let audio = null;              // 서버 TTS 재생용
  let leadSilenceCancel = null;  // 재생 중인 선행 무음의 cancel() — stop() 이 즉시 끊을 때 쓴다.

  // 선행 무음을 재생하고 끝날 때까지 기다린다. stop() 이 무음 재생 도중 불리면 false 를
  // 반환하므로, 호출부는 이때 말소리를 시작하지 않아야 한다(무음 뒤에 뒤늦게 말이 나오는 것 방지).
  async function playLeadSilenceGate() {
    let cancelled = false;
    const { promise, cancel } = playLeadSilence();
    leadSilenceCancel = () => { cancelled = true; cancel(); };
    await promise;
    leadSilenceCancel = null;
    return !cancelled;
  }

  function pickVoice() {
    const ko = synth ? dedupeVoices(synth.getVoices()) : [];
    return preferredVoice(ko) || ko[0] || null;
  }
  if (synth) {
    voice = pickVoice();
    synth.onvoiceschanged = () => { if (!voice) voice = pickVoice(); };
  }

  // 서버(OpenAI) TTS: mp3 를 받아 재생한다. 실패하면 브라우저 음성으로 한 번 대체한다.
  // pronounced=true 면 이미 서버가 낭독용으로 변환한 문장이므로 speakable() 을 다시 적용하지
  // 않는다(중복 적용 방지 — /api/tts 자체도 서버에서 한 번 더 변환하지만 멱등이라 안전하다).
  async function speakServer(text, pronounced) {
    if (!text) return;
    if (audio) { try { audio.pause(); } catch (_) {} audio = null; }
    let url;
    try {
      const res = await fetch("/api/tts", {
        method: "POST", headers: { "Content-Type": "application/json", ...getExtraHeaders() },
        body: JSON.stringify({ text: pronounced ? text : speakable(text) }),
      });
      if (!res.ok) throw new Error(`tts ${res.status}`);
      url = URL.createObjectURL(await res.blob());
    } catch (e) {
      console.warn("서버 TTS 실패, 브라우저 음성으로 대체:", e.message);
      mode = "browser";
      const p = api.speak(text, { pronounced });
      mode = "server";
      return p;
    }
    const ok = await playLeadSilenceGate();
    if (!ok) { URL.revokeObjectURL(url); return; }   // stop() 이 무음 재생 도중 호출됨 — 말소리를 시작하지 않는다
    await new Promise((resolve) => {
      audio = new Audio(url);
      audio.onended = () => { URL.revokeObjectURL(url); audio = null; resolve(); };
      audio.onerror = () => { URL.revokeObjectURL(url); audio = null; resolve(); };
      audio.onpause = () => { if (audio && audio.ended === false) resolve(); };   // stop() 으로 끊긴 경우
      audio.play().catch(() => resolve());
    });
  }

  const api = {
    get supported() { return { recognition: !!SR, synthesis: !!synth }; },
    listVoices() { return synth ? dedupeVoices(synth.getVoices()) : []; },
    // 미리듣기: 현재 선택과 무관하게 지정한 음성으로 짧은 문장을 읽는다.
    async previewVoice(v) {
      if (!synth || !v) return;
      synth.cancel();
      const ok = await playLeadSilenceGate();
      if (!ok) return;
      return new Promise((resolve) => {
        const u = new SpeechSynthesisUtterance("안녕하세요, 모두몰 고객센터입니다.");
        u.lang = v.lang || lang;
        u.voice = v;
        u.rate = 1.0;
        u.onend = () => resolve();
        u.onerror = () => resolve();
        synth.speak(u);
      });
    },
    setVoice(v) { voice = v; },
    setMode(m) { mode = m === "server" ? "server" : "browser"; },
    get mode() { return mode; },

    listen() {
      return new Promise((resolve, reject) => {
        if (!SR) return reject(new Error("unsupported"));
        rec = new SR();
        rec.lang = lang;
        rec.interimResults = true;
        rec.continuous = false;
        rec.maxAlternatives = 1;
        let finalText = "";
        rec.onresult = (e) => {
          let interim = "";
          for (let i = e.resultIndex; i < e.results.length; i++) {
            const t = e.results[i][0].transcript;
            if (e.results[i].isFinal) finalText += t; else interim += t;
          }
          onInterim(interim || finalText);
        };
        rec.onerror = (e) => { rec = null; reject(new Error(e.error)); };
        rec.onend = () => { rec = null; resolve(finalText.trim()); };
        rec.start();
      });
    },

    // { pronounced: true } 는 서버가 이미 낭독용으로 변환한 문장(/api/call/start·turn 의
    // speech 필드)이라는 뜻 — speakable() 의 식별자 자릿수 읽기를 다시 적용하지 않는다.
    async speak(text, { pronounced = false } = {}) {
      if (mode === "server") return speakServer(text, pronounced);
      if (!synth || !text) return;
      synth.cancel();
      const ok = await playLeadSilenceGate();
      if (!ok) return;   // stop() 이 무음 재생 도중 호출됨 — 말소리를 시작하지 않는다
      return new Promise((resolve) => {
        const u = new SpeechSynthesisUtterance(pronounced ? text : speakable(text));
        u.lang = lang;
        if (voice) u.voice = voice;
        u.rate = 1.0;
        u.onend = () => { currentUtter = null; resolve(); };
        u.onerror = () => { currentUtter = null; resolve(); };
        currentUtter = u;
        synth.speak(u);
      });
    },

    stop() {
      if (rec) { try { rec.abort(); } catch (_) {} rec = null; }
      if (leadSilenceCancel) { leadSilenceCancel(); leadSilenceCancel = null; }
      if (synth) synth.cancel();
      currentUtter = null;
      if (audio) { try { audio.pause(); } catch (_) {} audio = null; }
    },
  };
  return api;
}
