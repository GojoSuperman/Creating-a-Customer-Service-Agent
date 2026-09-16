// 음성 인식·합성 모듈. listen / speak / stop 과 supported / listVoices / setVoice 를 노출한다.
// Web Speech API (크롬·엣지). 서버 TTS로 바꿀 때는 이 파일만 교체한다.
export function createVoice({ lang = "ko-KR", onInterim = () => {} } = {}) {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const synth = window.speechSynthesis;
  let rec = null;
  let currentUtter = null;
  let voice = null;

  function pickVoice() {
    const all = synth ? synth.getVoices() : [];
    const ko = all.filter(v => v.lang && v.lang.toLowerCase().startsWith("ko"));
    return ko[0] || all[0] || null;
  }
  if (synth) {
    voice = pickVoice();
    synth.onvoiceschanged = () => { if (!voice) voice = pickVoice(); };
  }

  return {
    get supported() { return { recognition: !!SR, synthesis: !!synth }; },
    listVoices() { return synth ? synth.getVoices().filter(v => v.lang.toLowerCase().startsWith("ko")) : []; },
    setVoice(v) { voice = v; },

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
      return new Promise((resolve) => {
        if (!synth || !text) return resolve();
        synth.cancel();
        const u = new SpeechSynthesisUtterance(text);
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
    },
  };
}
