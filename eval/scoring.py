# -*- coding: utf-8 -*-
"""정답셋 채점 규칙. LLM 없이 돈다."""
import json
import math
import re
from pathlib import Path

from server.guardrail import normalize_korean_myriad
from server.guardrail import normalize_korean_myriad
from server.pipeline import ASK_PATTERN, infer_action


def norm_num(s) -> str:
    return re.sub(r"(?<=\d),(?=\d)", "", str(s))


def score_tools(expect: dict, tools_called: list, action: str):
    """도구 호출 적절성. 요구사항 원문이 "정확히 일치"이므로 부족분(need - got)만 보던 예전 방식을
    버리고 **집합 동치**(need == got)로 판정한다 — 관련 없는 도구를 더 부른 것(extra)도 감점된다.
    이렇게 하면 "필요한 근거를 안 읽음"(missing)과 "관련 없는 근거를 훑음"(extra)이 모두 걸린다.

    action 도 이 지표에 포함시켰다: action 은 "도구를 부를지/에스컬레이션할지"의 판단이고, 이 도메인
    골든셋은 ESCALATE 기대 케이스의 tools 에 이미 escalate_to_agent 를 넣어 두므로(예: C-013, C-033)
    "넘겨야 하는데 지어낸 것"은 대개 tools 불일치로도 걸리지만, 우연히 tools 집합은 같은데 action만
    틀리는 경우(예: 같은 조회 도구를 부르고도 ANSWER 대신 ASK 로 얼버무림)까지 잡으려면 action 도
    도구 판단의 일부로 봐야 한다는 판단에서다. 답변 내용(must/forbid)은 여기서 보지 않는다 — 그건
    score_answer(답변 적절성)의 몫이다."""
    fails = []
    need, got = set(expect.get("tools", [])), set(tools_called)
    missing, extra = need - got, got - need
    if missing:
        fails.append(f"tools 미호출: {sorted(missing)}")
    if extra:
        fails.append(f"tools 과다호출: {sorted(extra)}")
    if expect["action"] != action:
        fails.append(f'action: 기대 {expect["action"]} != 실제 {action}')
    return (not fails), fails


def score_tools_legacy(expect: dict, tools_called: list, action: str):
    """도구 호출 적절성의 **옛 정의**(과거 62.5%·56.2% 수치와 같은 잣대). score_tools 가 "정확히
    일치"(집합 동치, extra 도 감점)로 엄격해지기 전에는 기대 도구 중 빠진 것(missing)만 보고 초과
    호출(extra, 예: 상품명→ID 해석에 쓰는 search_product)은 감점하지 않았다. 과거 기록과 비교하려면
    이 함수를, 새 요구사항(정확히 일치)을 보려면 score_tools 를 쓴다. action 판단은 두 정의에서
    동일하게 본다(도구를 부를지/에스컬레이션할지의 판단이라 "옛 정의"에서도 계속 봤었다)."""
    fails = []
    need, got = set(expect.get("tools", [])), set(tools_called)
    missing = need - got
    if missing:
        fails.append(f"tools 미호출: {sorted(missing)}")
    if expect["action"] != action:
        fails.append(f'action: 기대 {expect["action"]} != 실제 {action}')
    return (not fails), fails


def score_answer(expect: dict, answer: str):
    """답변 적절성. must(반드시 담아야 할 사실) 전부 포함 + forbid(말하면 안 되는 것) 전부 미포함이면
    1점. 표현이 아니라 사실을 보도록 숫자 표기(만/천 단위, 쉼표)를 정규화해서 비교한다.
    ASK 형식 체크(되묻는 문장인지)도 여기 포함한다 — "ASK 인데 안 되묻는 것"은 반드시 취해야 할
    응답 형태를 안 지킨 것이라 담아야 할 사실을 빠뜨린 것과 같은 성격으로 본다. tools/action 은
    여기서 보지 않는다 — 그건 score_tools(도구 호출 적절성)의 몫이다."""
    fails = []
    a = norm_num(normalize_korean_myriad(answer))   # "4만 원" 도 40000 으로 비교
    for m in expect.get("must", []):
        if norm_num(m) not in a:
            fails.append(f'must 누락: "{m}"')
    for f in expect.get("forbid", []):
        if norm_num(f) in a:
            fails.append(f'forbid 위반: "{f}"')
    if expect["action"] == "ASK" and not re.search(ASK_PATTERN, answer):
        fails.append("ASK 인데 되묻는 문장이 아님")
    return (not fails), fails


def score_turn(expect: dict, answer: str, tools_called: list, action: str):
    """종합 판정. 도구 호출 적절성(score_tools) + 답변 적절성(score_answer) 을 합쳐 하나의
    pass/fail 로 낸다. 기존 소비자(self_check, eval_regression 등)와의 하위 호환을 위해 남겨 둔다 —
    두 지표를 각각 보려면 score_tools/score_answer 를 따로 호출한다."""
    tool_ok, tool_fails = score_tools(expect, tools_called, action)
    ans_ok, ans_fails = score_answer(expect, answer)
    fails = tool_fails + ans_fails
    return (tool_ok and ans_ok), fails


def load_first_turns(goldenset_path: Path) -> list[dict]:
    gold = json.loads(Path(goldenset_path).read_text(encoding="utf-8"))
    cases = []
    for c in gold["conversations"]:
        q = next(t for t in c["turns"] if t["role"] == "customer")
        a = next((t for t in c["turns"] if t.get("expect")), None)
        if a:
            # 턴 레벨 route 우선, 없으면 첫 번째 호프 (멀티턴 대화에서 첫 턴은 첫 호프에 속함)
            route = a["expect"].get("route") or c["route"].split("→")[0].strip()
            cases.append({"conv_id": c["conv_id"], "question": q["text"],
                          "route": route, "expect": a["expect"]})
    return cases


def self_check(cases: list[dict]) -> list[str]:
    """모범 답안(reference)이 채점기를 통과하지 못하면 채점기가 틀린 것이다.
    도구 호출 적절성 + 답변 적절성 두 지표 모두를 본다(self_check_split 참고). 하나라도 실패하면
    이 목록에 잡힌다 — 기존 호출부(eval_answer.py 의 통과 게이트) 호환을 위해 conv_id 리스트로 낸다."""
    return [c["conv_id"] for c in cases
            if not score_turn(c["expect"], c["expect"]["reference"],
                              c["expect"].get("tools", []), c["expect"]["action"])[0]]


def self_check_split(cases: list[dict]) -> tuple[list[str], list[str]]:
    """self_check 를 두 지표로 나눠 본다. 모범 답안을 넣었을 때 두 지표 모두 만점이어야 채점기가
    맞는 것이다. (도구 호출 적절성 실패 conv_id 목록, 답변 적절성 실패 conv_id 목록) 을 반환한다."""
    tool_bad, ans_bad = [], []
    for c in cases:
        e = c["expect"]
        tool_ok, _ = score_tools(e, e.get("tools", []), e["action"])
        ans_ok, _ = score_answer(e, e["reference"])
        if not tool_ok:
            tool_bad.append(c["conv_id"])
        if not ans_ok:
            ans_bad.append(c["conv_id"])
    return tool_bad, ans_bad


def aggregate_runs(passes: list) -> str:
    """같은 케이스를 여러 번 돌린 결과를 하나로 판정한다.

    ceil(n * 2/3) 이상 통과면 PASS, 0 이면 FAIL, 그 사이면 FLAP(흔들림).
    FLAP 은 단언이 너무 엄격하거나 모델이 그 입력에서 불안정하다는 뜻이다. 지우지 말고 따로 본다.
    """
    n = len(passes)
    if n == 0:
        raise ValueError("실행 결과가 비어 있습니다")
    ok = sum(1 for p in passes if p)
    if ok >= math.ceil(n * 2 / 3):
        return "PASS"
    if ok == 0:
        return "FAIL"
    return "FLAP"


def load_regression_cases(path: Path) -> list[dict]:
    """회귀 테스트 케이스를 JSON 파일에서 로드한다."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return list(data["cases"])


def score_regression(expect: dict, answer: str, action: str) -> tuple:
    """회귀 케이스 채점. action 은 허용 목록 중 하나여야 하고, forbid 문자열이 답변에 없어야 한다."""
    fails = []
    allowed = expect["action"]
    if action not in allowed:
        fails.append(f"action: {action} 는 허용 목록 {allowed} 에 없음")
    a = norm_num(normalize_korean_myriad(answer))
    for f in expect.get("forbid", []):
        if norm_num(normalize_korean_myriad(f)) in a:
            fails.append(f'forbid 위반: "{f}"')
    return (not fails), fails


_KIND_PREFIX = (("action:", "action"), ("tools 미호출:", "tools 미호출"), ("tools 과다호출:", "tools 과다호출"),
                ("must 누락:", "must 누락"), ("forbid 위반:", "forbid 위반"), ("ASK 인데", "ASK 형식"))


def fail_kinds(fails: list) -> list:
    """score_turn 실패 문자열을 사유 종류로 분류한다(action/tools 미호출/tools 과다호출/must 누락/
    forbid 위반/ASK 형식). 리포트의 사유 분포에 쓴다."""
    kinds = []
    for f in fails:
        for prefix, kind in _KIND_PREFIX:
            if f.startswith(prefix):
                kinds.append(kind)
                break
    return kinds


def missing_must(fails: list) -> list:
    """'must 누락: "X"' 문자열에서 X 만 뽑는다."""
    return [m.group(1) for f in fails for m in [re.match(r'must 누락: "(.*)"$', f)] if m]


def needs_judge(fails: list) -> bool:
    """규칙 실패가 must 누락뿐일 때만 judge 대상이다. action·tools·forbid·ASK 형식 실패는 judge 로 완화하지 않는다."""
    kinds = fail_kinds(fails)
    return bool(kinds) and all(k == "must 누락" for k in kinds)
