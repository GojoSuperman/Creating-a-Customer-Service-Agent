# -*- coding: utf-8 -*-
"""⑤ 고객 컨텍스트 효과 측정. 실제 문의 N건을 (a) 비회원 (b) 식별된 고객 두 조건으로 흘려보내 자동화율을 비교한다.

.venv/bin/python -m eval.eval_context [--n 40] [--domain modumall]
문의마다 새 통화. (b) 는 정식 주문 고객 8명 중 하나를 무작위(seed 고정)로 발신자로 붙인다.
"""
import argparse
import random

import pandas as pd

from server.config import load_settings
from server.domain import load_domain
from server.pipeline import Pipeline
from server.repo import Repo


def run(pipeline, questions, phones):
    rows = []
    for q, ph in zip(questions, phones):
        cid, _, cust = pipeline.start_call(phone=ph)
        r = pipeline.turn(cid, q)
        pipeline.end_call(cid)
        rows.append({"q": q, "customer": bool(cust), "action": r.action, "tools": len(r.tools) > 0})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default=None)
    ap.add_argument("--n", type=int, default=40)
    a = ap.parse_args()
    s = load_settings()
    domain = load_domain(s.domains_root / (a.domain or s.domain))
    inq = pd.read_csv(domain.path / "eval" / "customer_inquiries.csv", encoding="utf-8-sig")
    rnd = random.Random(7)
    qs = rnd.sample(inq["question"].tolist(), a.n)
    repo = Repo(domain.db_path)
    phones = [repo.customer(repo.order(f"O-10{i:02d}")["customer_id"])["phone"] for i in range(1, 9)]
    pipeline = Pipeline(domain, s)
    a_df = run(pipeline, qs, [None] * a.n)
    b_df = run(pipeline, qs, [rnd.choice(phones) for _ in qs])
    for label, df in (("A. 비회원", a_df), ("B. 식별된 고객", b_df)):
        print(f"\n{label}  n={len(df)}")
        print(df["action"].value_counts().to_string())
        answered = df[df["action"] == "ANSWER"]
        print(f"자동화율(답변 도달) {len(answered) / len(df):.3f}  조회율(답변 중 도구 호출) {answered['tools'].mean() if len(answered) else 0:.3f}")


if __name__ == "__main__":
    main()
