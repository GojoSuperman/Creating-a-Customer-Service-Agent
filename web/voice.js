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
export function labelVoices(voices) {
  const list = voices || [];
  const byName = new Map();
  for (const v of list) {
    const arr = byName.get(v.name) || [];
    arr.push(v);
    byName.set(v.name, arr);
  }
  const labelByKey = new Map();
  for (const group of byName.values()) {
    if (group.length === 1) {
      labelByKey.set(voiceKey(group[0]), group[0].name);
      continue;
    }
    const bases = group.map((v) => {
      const svc = v.localService ? "로컬" : "온라인";
      return v.lang ? `${v.name} (${svc} · ${v.lang})` : `${v.name} (${svc})`;
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

export function createVoice({ lang = "ko-KR", onInterim = () => {}, getExtraHeaders = () => ({}) } = {}) {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const synth = window.speechSynthesis;
  let rec = null;
  let currentUtter = null;
  let voice = null;
  let mode = "browser";          // "browser" | "server"
  let audio = null;              // 서버 TTS 재생용

  function pickVoice() {
    const ko = synth ? dedupeVoices(synth.getVoices()) : [];
    return preferredVoice(ko) || ko[0] || null;
  }
  if (synth) {
    voice = pickVoice();
    synth.onvoiceschanged = () => { if (!voice) voice = pickVoice(); };
  }

  // 서버(OpenAI) TTS: mp3 를 받아 재생한다. 실패하면 브라우저 음성으로 한 번 대체한다.
  async function speakServer(text) {
    if (!text) return;
    if (audio) { try { audio.pause(); } catch (_) {} audio = null; }
    let url;
    try {
      const res = await fetch("/api/tts", {
        method: "POST", headers: { "Content-Type": "application/json", ...getExtraHeaders() },
        body: JSON.stringify({ text: speakable(text) }),
      });
      if (!res.ok) throw new Error(`tts ${res.status}`);
      url = URL.createObjectURL(await res.blob());
    } catch (e) {
      console.warn("서버 TTS 실패, 브라우저 음성으로 대체:", e.message);
      mode = "browser";
      const p = api.speak(text);
      mode = "server";
      return p;
    }
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
    previewVoice(v) {
      if (!synth || !v) return Promise.resolve();
      synth.cancel();
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

    speak(text) {
      if (mode === "server") return speakServer(text);
      return new Promise((resolve) => {
        if (!synth || !text) return resolve();
        synth.cancel();
        const u = new SpeechSynthesisUtterance(speakable(text));
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
      if (synth) synth.cancel();
      currentUtter = null;
      if (audio) { try { audio.pause(); } catch (_) {} audio = null; }
    },
  };
  return api;
}
