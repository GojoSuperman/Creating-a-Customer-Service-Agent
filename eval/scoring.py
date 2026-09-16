# -*- coding: utf-8 -*-
"""정답셋 채점 규칙. LLM 없이 돈다."""
import json
import re
from pathlib import Path

from server.pipeline import ASK_PATTERN, infer_action


def norm_num(s) -> str:
    return re.sub(r"(?<=\d),(?=\d)", "", str(s))


def score_turn(expect: dict, answer: str, tools_called: list, action: str):
    fails = []
    a = norm_num(answer)
    if expect["action"] != action:
        fails.append(f'action: 기대 {expect["action"]} != 실제 {action}')
    need = set(expect.get("tools", []))
    if need - set(tools_called):
        fails.append(f"tools 미호출: {sorted(need - set(tools_called))}")
    for m in expect.get("must", []):
        if norm_num(m) not in a:
            fails.append(f'must 누락: "{m}"')
    for f in expect.get("forbid", []):
        if norm_num(f) in a:
            fails.append(f'forbid 위반: "{f}"')
    if expect["action"] == "ASK" and not re.search(ASK_PATTERN, answer):
        fails.append("ASK 인데 되묻는 문장이 아님")
    return (not fails), fails


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
    """모범 답안(reference)이 채점기를 통과하지 못하면 채점기가 틀린 것이다."""
    return [c["conv_id"] for c in cases
            if not score_turn(c["expect"], c["expect"]["reference"],
                              c["expect"].get("tools", []), c["expect"]["action"])[0]]
