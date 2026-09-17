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

// 한국어 음성만 추리고, name+lang 이 같은 중복 항목은 하나만 남긴다.
// 주의: 어떤 음성이 실제로 소리를 내는지는 브라우저·OS·설치 상태에 따라 달라 UA 나
// localService 값만으로는 추측할 수 없다(엣지에서도 원격 음성이 재생되는 사례가 실측으로
// 확인됨). 그래서 여기서는 "될 것 같은 것"을 걸러내지 않고, 중복만 정리한다.
// 실제로 소리가 나는지는 미리듣기 버튼으로 사용자가 직접 확인한다.
export function dedupeVoices(voices) {
  const ko = (voices || []).filter(v => v.lang && v.lang.toLowerCase().startsWith("ko"));
  const seen = new Set();
  const deduped = [];
  for (const v of ko) {
    const key = `${v.name}|${v.lang}`;
    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push(v);
  }
  return deduped;
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
