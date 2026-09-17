// 하단 통계 패널: LLM 채점 없이, 화면이 매 턴 이미 받는 값(route, confidence, action, tools,
// guardrail, elapsed_ms)만으로 통화 누적 수치를 계산해 보여준다. 네트워크 호출은 하지 않는다.
// 교육자료(.superpowers/수업정리/index.html 실습 ⑩)의 두 축을 그대로 따른다 — 총점으로 합치지 않는다.
import { THRESHOLD } from "./panel.js";

// server/guardrail.py 의 위반 유형 문자열과 동일해야 턴 로그의 violations[].type 과 매칭된다.
const VIOLATION_TYPES = ["출처 불명 수치", "툴 미호출 단정", "미확정값 확답", "진행 중 상태 누락"];

export function createStatsPanel(root) {
  let stats = null;   // null = 통화 중 아님

  function esc(s) { return String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

  function empty() {
    stats = null;
    root.innerHTML = `<div class="stats-empty muted">통화를 시작하면 집계가 표시됩니다.</div>`;
  }

  function reset() {
    stats = {
      total: 0, sumMs: 0, maxMs: 0, routes: {},
      violationCounts: {},
      lowConfAnswer: 0,
      noToolAnswer: 0, askEnd: 0, escalateEnd: 0, answered: 0,
    };
    render();
  }

  // r: web/call.js 가 /api/call/turn 응답 그대로 넘기는 객체(route, confidence, action, tools, guardrail, elapsed_ms)
  function addTurn(r) {
    if (!stats) reset();
    stats.total += 1;
    const ms = Number(r.elapsed_ms) || 0;
    stats.sumMs += ms;
    if (ms > stats.maxMs) stats.maxMs = ms;
    const route = r.route || "OTHER";
    stats.routes[route] = (stats.routes[route] || 0) + 1;

    const violations = (r.guardrail && r.guardrail.violations) || [];
    for (const v of violations) {
      const t = v.type || "기타";
      stats.violationCounts[t] = (stats.violationCounts[t] || 0) + 1;
    }

    const action = r.action;
    const tools = r.tools || [];
    if (action === "ANSWER") {
      stats.answered += 1;
      if (!tools.length) stats.noToolAnswer += 1;
      if (r.confidence != null && r.confidence < THRESHOLD) stats.lowConfAnswer += 1;
    } else if (action === "ASK") {
      stats.askEnd += 1;
    } else if (action === "ESCALATE" || action === "OUT_OF_SCOPE") {
      stats.escalateEnd += 1;
    }
    render();
  }

  function num(n, hitClass) {
    return `<span class="stat-num ${n > 0 ? hitClass : "stat-zero"}">${n}</span>`;
  }

  function row(label, n, hitClass) {
    return `<div class="stat-row"><span>${esc(label)}</span>${num(n, hitClass)}</div>`;
  }

  function render() {
    if (!stats) { empty(); return; }
    const s = stats;

    const violationRows = VIOLATION_TYPES.map(t => row(t, s.violationCounts[t] || 0, "stat-bad")).join("");
    const extraTypes = Object.keys(s.violationCounts).filter(t => !VIOLATION_TYPES.includes(t));
    const extraRows = extraTypes.map(t => row(t, s.violationCounts[t], "stat-bad")).join("");

    const routeEntries = Object.entries(s.routes);
    const routesHtml = routeEntries.length
      ? routeEntries.map(([r, n]) => `<span class="stat-route">${esc(r)} ${n}</span>`).join(" ")
      : `<span class="muted">-</span>`;
    const avgMs = s.total ? Math.round(s.sumMs / s.total) : 0;

    root.innerHTML = `
      <div class="stat-meta muted">누적 ${s.total}턴</div>
      <div class="stats-axes score-axes">
        <div class="score-axis safety">
          <h4>안전</h4>
          <div class="sub">실패하면 고객이 틀린 답을 받습니다</div>
          ${violationRows}${extraRows}
          ${row("저확신인데 단정 답변", s.lowConfAnswer, "stat-bad")}
        </div>
        <div class="score-axis ability">
          <h4>능력</h4>
          <div class="sub">실패하면 고객이 답을 못 받습니다</div>
          ${row("도구 호출 없이 답변", s.noToolAnswer, "stat-warn")}
          ${row("되묻기(ASK)로 종료", s.askEnd, "stat-warn")}
          ${row("이관·범위 밖으로 종료", s.escalateEnd, "stat-warn")}
          ${row("답변 도달", s.answered, "")}
        </div>
      </div>
      <div class="stats-ref">
        <h4>참고</h4>
        <div class="stat-row"><span>총 턴 수</span><span>${s.total}</span></div>
        <div class="stat-row"><span>평균 응답 시간</span><span>${avgMs} ms</span></div>
        <div class="stat-row"><span>최대 응답 시간</span><span>${s.maxMs} ms</span></div>
        <div class="stat-row stat-routes"><span>라우트별 분포</span><span>${routesHtml}</span></div>
      </div>`;
  }

  empty();
  return { reset, addTurn };
}
