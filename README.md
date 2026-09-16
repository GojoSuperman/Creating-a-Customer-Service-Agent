# 음성 고객 응대 에이전트

브라우저에서 전화처럼 대화하는 쇼핑몰 상담 에이전트. 문의를 **분류**하고, 목 DB를 **조회**하고,
매뉴얼에 **근거**해 답하며, **가드레일**이 출처 없는 숫자를 막는다.

## 시작하기

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
cp .env.example .env            # OPENAI_API_KEY 채우기
.venv/bin/python -m server      # http://127.0.0.1:8000  (크롬 권장, 포트 변경은 PORT=8010)
```

`.env`에 실제 `OPENAI_API_KEY`를 입력해야 `python -m server` 실행과 LLM 평가 스크립트가 작동합니다.

## 구조

| 경로 | 역할 |
|---|---|
| `server/router.py` | 분류 그래프 (classify → gate) |
| `server/context.py` | 매뉴얼 장 단위 분할, 라우트별 컨텍스트 |
| `server/tools.py` | 조회 도구 9개 |
| `server/answer.py` | 도구 호출 루프 |
| `server/guardrail.py` | 숫자 출처 역추적, 미확정값 확답 검사 |
| `server/pipeline.py` | 전체 그래프 + 통화별 체크포인터 |
| `server/app.py` | FastAPI |
| `web/` | 전화 화면 (voice.js 가 음성 모듈 — 브라우저/OpenAI TTS 전환) |
| `domains/<이름>/` | 도메인 데이터 |
| `eval/` | 평가 스크립트 |

## 도메인 바꾸기

`domains/<새이름>/`에 `domain.json`, `policy.md`, `mockdb.json`, `eval/`을 같은 스키마로 만들고
`.env`에 `DOMAIN=<새이름>`. 라우트 이름 5개와 도구 9개, 목 DB 스키마는 고정이다.

## 평가

```bash
.venv/bin/python -m eval.eval_router --rule      # 규칙 기준선 (키 불필요)
.venv/bin/python -m eval.eval_router             # LLM 라우터 120건
.venv/bin/python -m eval.eval_hard --rule        # 어려운 케이스 72건, 되묻기 정책 비교 (키 불필요)
.venv/bin/python -m eval.eval_answer --workers 1 # 정답셋 첫 턴 34건 중 자동 판정 가능한 32건 (--runs 3 플랩 감지; gpt-4.1 TPM 30k 한도라 workers 1 권장)
.venv/bin/python -m eval.eval_regression --runs 3   # 인젝션·없는 ID 회귀 스위트 (키 필요)
.venv/bin/pytest -q                              # 단위 테스트 (키 불필요)
```

한 군데 고치고 → 평가 → 숫자가 어디로 움직였나 본다. 평가셋(`split == "eval"`)은 절대 프롬프트에 넣지 않는다.

| # | 바꾼 것 | 라우팅 macro F1 | 답변 통과율 | 회귀 통과 | 메모 |
|---|---|---|---|---|---|
| 0 | 기준선 (규칙 라우터) | 0.677 | – | – | 정확도 0.583 |
| 1 | LLM 라우터 (gpt-4.1-mini) + 답변 gpt-4.1 | 0.945 | 43.8% (14/32, runs=3) | 6/6 PASS ×3회 | 정확도 0.933, ECE 0.039 · hard 72건 자동처리 0.611, 경계모호 15건 전부 확신 처리(위험) |
| 2 | 검색 동점 버그 수정 · 채점기 만 단위 정규화 · 도구 상한 4 · 주문번호 우선 조회 | 0.945 | 50.0% (16/32, runs=3, FLAP 2) | 6/6 | 남은 실패: 되물어야 할 때 답변 3건, must 문자열 의미 불일치 6건(품절/불가/925), 도구 미호출 6건 |

## 수동 검증 체크리스트

- [ ] "배송비 얼마예요?" → 상품을 되묻고 기본 배송비 2,500원만 안내하는가
- [ ] "청바지 반품되나요?" → 없는 상품을 있다고 하지 않는가
- [ ] "O-1006 반품 배송비 누가 내요?" → 검품 전이라 단정하지 않는가
- [ ] "홍대점 몇 시까지 해요?" → 범위 밖 안내 후 통화가 끊기는가
- [ ] "ㅂㅐ송비얼마임?" → 알아듣는가
- [ ] 1턴 "캔버스화 살 건데요" → 2턴 "배송비는요?" 가 앞 상품을 이어받는가

## 한계

- 상품명 연결이 토큰 겹침 + 오타 보정 수준이다. 실서비스는 임베딩 검색이 필요하다.
- 체크포인터는 메모리라 서버 재시작 시 통화가 사라진다.
- 음성 인식은 크롬·엣지의 Web Speech API에 의존한다. 음성 합성은 OpenAI TTS(`TTS_MODEL`, 기본 gpt-4o-mini-tts)를 쓰고, 비우면 브라우저 음성으로 돌아간다. 브라우저 음성은 엣지에서 'Online (Natural)' 계열이 자연스럽다.

모델명은 날짜 고정 버전을 쓰는 것이 안전하다(`ROUTER_MODEL=gpt-4.1-mini-2025-04-14` 처럼). 제공사가 별칭의 가중치를 조용히 바꾸면 회귀 스위트로만 알 수 있다.
