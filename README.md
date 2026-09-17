# 음성 고객 응대 에이전트

브라우저에서 전화처럼 대화하는 쇼핑몰 상담 에이전트. 문의를 **분류**하고, 목 DB를 **조회**하고,
매뉴얼에 **근거**해 답하며, **가드레일**이 출처 없는 숫자를 막는다.

## 시작하기

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt -r requirements-dev.txt
cp .env.example .env            # OPENAI_API_KEY 채우기
.venv/bin/python -m server      # http://127.0.0.1:8000  (크롬 권장, 포트 변경은 PORT=8010)
```

`requirements.txt`는 서버 실행에 필요한 런타임 의존성만 담는다. `requirements-dev.txt`는 그 위에
평가 스크립트(`eval/`)·테스트가 쓰는 `pandas`·`scikit-learn`·`pytest`·`httpx`를 더한다. 서버만
띄울 거면 `-r requirements.txt`만 설치해도 된다.

`.env`에 실제 `OPENAI_API_KEY`를 입력해야 `python -m server` 실행과 LLM 평가 스크립트가 작동합니다.

데이터는 첫 실행 시 자동 생성됩니다(`domains/modumall/modumall.db`). 다시 만들려면
`python -m server.db.generate --force`.

**운영 주의**
- `pytest` 를 돌리면 `tests/test_generate.py` 가 `domains/modumall/modumall.db` 를 재생성한다(파일 삭제 후 재작성). 데모 서버가 떠 있는 상태에서 테스트를 돌리면 그 서버는 삭제된 파일을 붙든 채 계속 돌아가 화면과 DB 가 어긋나므로(예: 서버가 발급한 주문이 DB 에 없음), 테스트 후에는 서버를 재시작해야 한다.
- `/shop` 은 비밀번호 없이 전화번호만으로 로그인한다. 번호를 아는 사람은 그 고객의 주문·주소를 전부 볼 수 있다. 로컬 개발 시 서버는 `127.0.0.1` 바인딩(기본값)을 유지하고 외부에 노출하지 말 것. (레일웨이 배포는 통화 화면·쇼핑몰을 의도적으로 공개하는 결정이며 아래 "배포(Railway)" 절 참고.)

## 배포(Railway)

데이터는 볼륨 없이 **매 배포마다 초기화**된다. `domains/modumall/modumall.db` 는 기동 시
`server/domain.py`의 `generate(path)` 가 자동으로 새로 만든다.

**빌드 방식**: 저장소 루트의 `Dockerfile`을 쓴다(레일웨이가 자동 인식하거나 `railway.toml`의
`builder = "DOCKERFILE"`로 명시). 런타임 의존성만 설치하므로(`requirements.txt`, 평가·테스트
전용 `pandas`/`scikit-learn`/`pytest`는 제외) 이미지가 가볍다.

**환경변수**

| 변수 | 필수 | 기본값 | 설명 |
|---|---|---|---|
| `HOST` | 레일웨이에서 필수 | `127.0.0.1` | 외부 접속을 받으려면 `0.0.0.0`으로 설정. Dockerfile 이 이미 `0.0.0.0`으로 지정해 둔다. |
| `PORT` | 레일웨이가 자동 주입 | `8000` | 레일웨이가 컨테이너에 주입하는 포트를 그대로 따른다. |
| `ADMIN_PASSWORD` | **필수** | (없음) | `/admin/*` 전체에 HTTP Basic 인증을 건다(사용자명 `admin`, 비밀번호는 이 값). **fail-closed**: 비워두거나 설정하지 않으면 어드민이 무인증으로 열리는 대신 `/admin/*` 전체가 503(어드민 비밀번호가 설정되지 않았습니다)으로 막힌다. 공개 배포에서는 반드시 설정할 것 — 환경변수 오타 한 번으로 고객 개인정보가 열리는 사고를 막기 위한 설계다. |
| `ALLOW_OPEN_ADMIN` | 선택(로컬 전용) | (없음) | `1`로 설정하면 `ADMIN_PASSWORD` 없이도 어드민을 무인증으로 연다. 로컬 개발 편의용 명시적 옵트인이며, 공개 배포에는 절대 설정하지 말 것. |
| `OPENAI_API_KEY` | 선택 | (없음) | 서버 쪽 폴백 키. 없어도 서비스는 동작한다 — 방문자가 통화 화면 설정 모달에 자기 OpenAI 키를 넣으면(BYOK, 요청마다 `X-OpenAI-Key` 헤더로 전달) 그 키로 동작한다. 둘 다 없으면 `/api/call/turn`·`/api/tts` 가 401 을 돌려주고 화면이 설정 모달을 연다. **순수 BYOK 로 운영하려면(서버가 키를 전혀 들고 있지 않으려면) 이 변수를 아예 설정하지 않으면 된다.** |
| `TTS_MODEL` 등 | 선택 | `.env.example` 참고 | `TTS_MODEL`, `TTS_VOICE`, `ROUTER_MODEL`, `ANSWER_MODEL`, `CONF_THRESHOLD` 등 나머지 값은 로컬과 동일하게 선택 사항이다. |

**방문자 키(BYOK)**: 통화 화면 제목 줄의 설정 버튼을 누르면 모달이 뜬다. 여기 입력한 OpenAI 키는
브라우저 `localStorage` 에만 저장되고, 서버로는 요청마다 `X-OpenAI-Key` 헤더로만 전달된다(서버
환경변수를 건드리지 않으므로 동시 접속자끼리 키가 섞이지 않는다). 모달의 "연결 확인" 버튼은
`/api/key/check` 로 키 유효성만 짧게 확인한다(키 원문·OpenAI 오류 원문은 응답에 담지 않는다).
`/api/call/turn`·`/api/tts` 가 401 을 돌려주면(헤더 키도 서버 `OPENAI_API_KEY` 폴백도 없을 때)
화면이 자동으로 이 모달을 연다.

**보안 범위**: 어드민만 비밀번호로 보호된다. 통화 화면(`/`)과 쇼핑몰(`/shop/*`)은 인증 없이 공개된다
(위 운영 주의 항목의 로컬 전용 경고는 레일웨이 배포에는 적용되지 않는, 의도된 설계다).

**남는 수동 작업(레일웨이 대시보드)**
- 프로젝트 생성 후 이 저장소를 연결하고 `HOST=0.0.0.0`, `ADMIN_PASSWORD`(필수) 등 환경변수를 등록한다. `OPENAI_API_KEY` 는 서버 쪽 폴백을 두고 싶을 때만 등록한다.
- 볼륨을 추가하지 않는다(데이터 초기화가 의도된 동작).
- 배포 후 `/admin` 접속 시 Basic 인증 프롬프트가 뜨는지, `/`·`/shop`은 그대로 열리는지 확인한다. `ADMIN_PASSWORD` 를 빠뜨리고 배포했다면 `/admin` 이 503 을 돌려주는지도 확인한다(무인증으로 열리면 안 된다).

## 배포(Render, 무료 등급)

레일웨이 체험판이 만료된 경우의 대안. 위 "배포(Railway)" 절의 내용(초기화되는 데이터, 환경변수
의미, BYOK, 보안 범위)은 그대로 적용되고, 여기서는 Render 고유의 차이만 적는다.

**Blueprint**: 저장소 루트의 `render.yaml`이 무료 등급 Docker 웹 서비스를 선언한다(브랜치
`feat/voice-agent`, 빌드는 `Dockerfile` 그대로 사용). Render 대시보드에서 "New +" → "Blueprint"로
이 저장소를 연결하면 `render.yaml`을 읽어 서비스를 자동 생성한다. 포트는 Render 가 컨테이너에
`PORT` 환경변수로 주입하고(`server/__main__.py`가 이미 읽음), `render.yaml`이 `HOST=0.0.0.0`을
지정해 둔다.

**대시보드에서 할 일**
1. Render 대시보드 → "New +" → "Blueprint" → 이 GitHub 저장소(`GojoSuperman/Creating-a-Customer-Service-Agent`) 선택.
2. 브랜치가 `feat/voice-agent`인지 확인하고 Blueprint 적용(Apply).
3. 생성된 서비스의 Environment 탭에서 `ADMIN_PASSWORD`(필수) 값을 채운다. `OPENAI_API_KEY`는 서버 쪽 폴백을 두고 싶을 때만 채운다(둘 다 `render.yaml`에 `sync: false`로 선언돼 있어 대시보드 입력을 기다린다).
4. 첫 배포가 끝나면 `/admin`이 Basic 인증을 요구하는지, `ADMIN_PASSWORD`를 빠뜨렸다면 503을 돌려주는지 확인한다(레일웨이 절의 확인 항목과 동일).

**환경변수**(`render.yaml`이 이미 선언; 값만 대시보드에서 채우면 됨)

| 변수 | render.yaml 상태 | 설명 |
|---|---|---|
| `HOST` | `0.0.0.0`로 고정 | Render 는 외부에서 컨테이너로 접속하므로 필요. |
| `PORT` | Render 가 자동 주입(선언 없음) | 컨테이너가 리슨할 포트. `server/__main__.py`가 `os.environ["PORT"]`를 읽는다. |
| `ADMIN_PASSWORD` | `sync: false`(대시보드에서 입력 필요) | 위 레일웨이 절과 동일하게 fail-closed. |
| `OPENAI_API_KEY` | `sync: false`(선택) | 위 레일웨이 절과 동일하게 선택 사항(BYOK로 대체 가능). |

**무료 등급 제약**
- 인스턴스 사양은 0.5 CPU / 512MB RAM. 기동 직후 RSS 를 별도 측정(런타임 의존성만 설치한 venv, 8010 이 아닌 포트)한 결과 약 **96~98MB**로, 512MB 한도에 여유가 있다(langchain/langgraph/fastapi/uvicorn 임포트와 SQLite 생성 포함, `/`·`/api/domain` 요청까지 받은 뒤 측정).
- 무료 등급은 트래픽이 없으면 인스턴스가 잠들고, 다음 요청이 오면 다시 깨운다 — **첫 요청이 수십 초까지 느릴 수 있다**(콜드 스타트). 이 앱은 기동 시 DB 를 새로 생성하므로 콜드 스타트가 더 걸릴 수 있음을 감안할 것.
- 볼륨을 붙이지 않는다(레일웨이와 동일하게 데이터 초기화가 의도된 동작이고, 무료 등급은 영구 디스크를 애초에 지원하지 않는다).

## 구조

| 경로 | 역할 |
|---|---|
| `server/router.py` | 분류 그래프 (classify → gate) |
| `server/context.py` | 매뉴얼 장 단위 분할, 라우트별 컨텍스트 |
| `server/tools.py` | 조회 도구 9개 |
| `server/answer.py` | 도구 호출 루프 |
| `server/guardrail.py` | 숫자 출처 역추적, 미확정값 확답 검사 |
| `server/pipeline.py` | 전체 그래프 + 통화별 체크포인터 |
| `server/db/` | 실측 기반 목 데이터 생성기 (SQLite) |
| `server/repo.py` | SQLite 읽기 저장소 계층 |
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
.venv/bin/python -m eval.eval_hard --rule        # 어려운 케이스 72건, 되묻기 정책 비교 + 임계값×마진 격자 (키 불필요)
.venv/bin/python -m eval.eval_answer --workers 1 # 정답셋 첫 턴 34건 중 자동 판정 가능한 32건 (--runs 3 플랩 감지; gpt-4.1 TPM 30k 한도라 workers 1 권장)
.venv/bin/python -m eval.eval_regression --runs 3   # 인젝션·없는 ID 회귀 스위트 (키 필요)
.venv/bin/python -m eval.eval_context            # 고객 컨텍스트 효과: 비회원 vs 식별된 고객 자동화율 비교 (키 필요)
.venv/bin/python -m eval.eval_answer --workers 1 --runs 3 --judge   # 규칙 실패의 must 누락 건을 LLM judge 로 재판정 (JUDGE_MODEL)
.venv/bin/python -m eval.eval_router --multiturn [--no-history] [--inherit]   # 멀티턴 라우트 드리프트 (20대화, 키 필요; --inherit 는 이어받기 실험 재현용)
.venv/bin/pytest -q                              # 단위 테스트 (키 불필요)
```

한 군데 고치고 → 평가 → 숫자가 어디로 움직였나 본다. 평가셋(`split == "eval"`)은 절대 프롬프트에 넣지 않는다.

| # | 바꾼 것 | 라우팅 macro F1 | 답변 통과율 | 회귀 통과 | 메모 |
|---|---|---|---|---|---|
| 0 | 기준선 (규칙 라우터) | 0.677 | – | – | 정확도 0.583 |
| 1 | LLM 라우터 (gpt-4.1-mini) + 답변 gpt-4.1 | 0.945 | 43.8% (14/32, runs=3) | 6/6 PASS ×3회 | 정확도 0.933, ECE 0.039 · hard 72건 자동처리 0.611, 경계모호 15건 전부 확신 처리(위험) |
| 2 | 검색 동점 버그 수정 · 채점기 만 단위 정규화 · 도구 상한 4 · 주문번호 우선 조회 | 0.945 | 50.0% (16/32, runs=3, FLAP 2) | 6/6 | 남은 실패: 되물어야 할 때 답변 3건, must 문자열 의미 불일치 6건(품절/불가/925), 도구 미호출 6건 |
| 3 | A단계: SQLite 데이터(상품 180·주문 480) + 발신번호 고객 식별 + 마무리 인사 ASK 오판 수정 | 0.945 | (재측정 예정) | 6/6 | eval_context 40건: 자동화율 비회원 0.350 → 식별 고객 0.450, 되묻기율 0.525 → 0.400, 조회율 0.571 → 0.778 |
| 4 | 3차 강화: judge 채점기 · 필수 도구 표(부정 결과 → revert) · 확신도 마진 게이트(CONF_MARGIN=0.3) · followup 라우트 이어받기(FOLLOWUP_INHERIT, 기본 꺼짐) · 이관 도구 매핑 · 동의어 보강 | 0.937 | 규칙 50.0% / judge 포함 62.5% (20/32, runs=3, FLAP 2) | 6/6 ×3 | judge 판정기 효과 = 바뀌지 않은 에이전트에 judge 만 붙여 규칙 14/32 → judge 포함 21/32(65.6%). 라우트별 필수 도구 표 + 자기 점검 프롬프트는 부정 결과(규칙 13/32·표만 12/32, 기준선 14/32)라 revert. 경계모호 위험 15 → 10건(Task 4 격자 측정 시점 8건)으로 목표 5건 미달, 격자에 5건 이하 조합이 없음. 멀티턴 문맥 주입은 순효과 −1건(턴2+ 정확도 기준선 0.957 → 0.913)이라 이어받기 기본 꺼짐. 측정 원본: docs/측정기록/2026-09-17 3차 강화 측정.md |
| 5 | 진행 상태 정합성: 반품·교환 status_detail 을 반품 단계에서 파생 + 종결 상태(반품완료·교환완료) 신설 · get_order_status 에 active_process 필드 노출 · 답변 규칙 5·6(현재 상태 우선순위, 진행 중 사실 먼저 안내) · 가드레일 VIOLATION_STALE_STATE(SHIPPING·RETURN_REFUND 한정, 재시도해도 안 되면 통화 안 끊음) | 0.937 (동일) | 규칙 46.9% / judge 포함 **56.2%**(18/32, runs=3, FLAP 2) — 기준선 62.5% 대비 **하락 6.3%p, 원인 미결** | 6/6 (동일) | 실통화 재현 O-1072 4/4 성공(반품 사실 우선 안내). 하락 원인을 가르려 규칙 5·6 만 뺀 A/B 재측정을 시도했으나 OpenAI 429(TPM 한도)로 중도 종료 — 데이터·도구 변경 탓인지 규칙 탓인지 가리지 못함. eval_answer 는 Answerer 직접 호출이라 가드레일을 안 타므로 가드레일 효과는 이 수치에 안 잡힘(재현·회귀로만 확인). 측정 원본: docs/측정기록/2026-09-17 진행상태 정합성 측정.md |

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
- 전화번호만으로 고객을 식별한다(본인 인증 없음). 환불 계좌 변경 같은 민감 처리는 이관으로 뺀다.
- judge 는 must 누락 건만 보며, rubric·reference 가 틀리면 judge 도 틀린다. 자기 검증(reference 전부 통과)이 그 최소 방어다.
- 라우터 과신(경계모호 15건이 conf 0.7~0.9)이 마진 게이트의 한계이며, 최종 측정에서 남은 10건(격자 측정 시점 8건, 실행 간 변동)은 2순위 확신도 유도 프롬프트나 경계모호 전용 신호가 필요하다.
- 라우터 입력의 [직전 문의]·[직전 라우트] 헤더는 고객 발화로 위조될 수 있다. 영향은 라우트 선택과 is_followup 에 한정되고 이어받기가 기본 꺼져 있어 라우트가 강제되지는 않는다.
- 음성 인식은 크롬·엣지의 Web Speech API에 의존한다. 음성 합성은 기본이 브라우저 음성(무료)이며, 엣지에서는 "Online (Natural)" 계열 한국어 음성을 자동으로 고른다. `TTS_MODEL=gpt-4o-mini-tts`를 설정하면 화면에서 OpenAI 음성(유료)을 선택할 수 있다.

모델명은 날짜 고정 버전을 쓰는 것이 안전하다(`ROUTER_MODEL=gpt-4.1-mini-2025-04-14` 처럼). 제공사가 별칭의 가중치를 조용히 바꾸면 회귀 스위트로만 알 수 있다.
