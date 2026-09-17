# 음성 고객 응대 에이전트

브라우저에서 전화처럼 대화하는 쇼핑몰 상담 에이전트. 문의를 **분류**하고, 목 DB를 **조회**하고,
매뉴얼에 **근거**해 답하며, **가드레일**이 출처 없는 숫자를 막는다.

## 라이브 데모

**https://creating-a-customer-service-agent.onrender.com**

- `/` — 통화 화면(전화처럼 대화)
- `/shop` — 쇼핑몰(로그인 후 주문 조회·구매)
- `/admin` — 어드민 콘솔(HTTP Basic 인증으로 보호됨)

무료 등급(Render)이라 **트래픽이 없으면 인스턴스가 잠들고, 첫 접속 시 30~40초 정도 걸려 깨어난다**
(콜드 스타트). "안 열린다"가 아니라 깨어나는 중일 가능성이 높으니 잠시 기다렸다가 새로고침할 것.

실제로 통화를 걸어 보려면(LLM 분류·답변을 태우려면) 통화 화면 제목 줄의 ⚙ 설정 버튼을 눌러
**본인의 OpenAI 키를 직접 입력**해야 한다(BYOK). 이 키는 브라우저 `localStorage`에만 저장되고
요청마다 헤더로만 전달되며, 서버·DB·로그 어디에도 남지 않는다(자세한 동작은 아래 "배포(Render,
무료 등급)" 절의 "방문자 키(BYOK)" 참고).

## 시작하기

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt -r requirements-dev.txt
cp .env.example .env            # 키 없이도 서버는 뜬다. 실제 통화를 태우려면 OPENAI_API_KEY 를 채운다.
.venv/bin/python -m server      # http://127.0.0.1:8000  (크롬 권장, 포트 변경은 PORT=8010)
```

`uv`가 없다면 [공식 설치 스크립트](https://docs.astral.sh/uv/getting-started/installation/)
(`curl -LsSf https://astral.sh/uv/install.sh | sh`)를 쓰거나, 아래처럼 표준 `venv` + `pip`으로도
동일하게 설치할 수 있다(직접 실행해 확인함):

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
```

`requirements.txt`는 서버 실행에 필요한 런타임 의존성만 담는다. `requirements-dev.txt`는 그 위에
평가 스크립트(`eval/`)·테스트가 쓰는 `pandas`·`scikit-learn`·`pytest`·`httpx`를 더한다. 서버만
띄울 거면 `-r requirements.txt`만 설치해도 된다.

**`OPENAI_API_KEY` 없이도 확인할 수 있는 것과, 있어야만 되는 것을 구분한다.**

| 키 없이 됨 | 키가 필요함 |
|---|---|
| 서버 기동, 통화 화면·쇼핑몰·어드민 열람 | 실제 통화(LLM 분류·답변 생성) |
| `pytest` 499건 전부 통과 | LLM 기반 평가(`eval_answer`, `--judge`, `eval_context` 등) |
| 규칙 기반 라우터 평가(`eval_router --rule`, `eval_hard --rule`) | |

즉 `.env`에 `OPENAI_API_KEY`를 채우지 않아도 서버는 정상 기동하고, 화면·쇼핑몰·어드민을 전부
둘러볼 수 있으며 테스트도 전부 통과한다. 실제로 LLM 이 분류·답변하는 통화를 걸려면(또는 방문자가
BYOK 로 키를 넣지 않는 로컬 개발에서) 키가 필요하다. 서버 코드상 키가 없을 때는 `/api/call/turn`·
`/api/tts` 가 401 을 돌려줄 뿐, 서버 자체가 죽지는 않는다(`server/app.py`의 `turn()` 참고).

데이터는 첫 실행 시 자동 생성됩니다(`domains/modumall/modumall.db`). 다시 만들려면
`python -m server.db.generate --force`.

**`pytest` 는 DB를 재생성한다 — 서버를 띄운 채 돌리면 화면과 DB가 어긋난다**
`pytest` 를 돌리면 `tests/test_generate.py` 가 `domains/modumall/modumall.db` 를 재생성한다(파일
삭제 후 재작성). 데모 서버가 떠 있는 상태에서 테스트를 돌리면 그 서버는 삭제된 파일을 붙든 채 계속
돌아가 화면과 DB 가 어긋나므로(예: 서버가 발급한 주문이 DB 에 없음), 테스트 후에는 서버를 재시작해야
한다.

**운영 주의**
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

## 카테고리 설계와 근거 문서 매핑

분류 라우트는 5개다(`ORDER_PLACE`·`PRODUCT_INFO`·`SHIPPING`·`RETURN_REFUND` + 미분류 카테고리
`OTHER`). 각 라우트는 `domains/modumall/domain.json`의 `routes[*].sections`로 매뉴얼
(`domains/modumall/policy.md`)의 특정 장(章)에 묶여 있고, 모든 라우트에 공통으로 들어가는 장은
`always_sections`로 따로 선언한다.

| 라우트 | 정의 | 매뉴얼 근거 장 |
|---|---|---|
| `ORDER_PLACE` | 구매·주문 접수, 구매 가능 여부, 수량·옵션 변경 요청 | 2. 주문·구매 문의 |
| `PRODUCT_INFO` | 상품 구성·품질·소재·사이즈·재고 문의 | 3. 상품 문의 |
| `SHIPPING` | 배송비, 배송 기간, 배송 조회, 무료배송 조건 | 4. 배송 문의 |
| `RETURN_REFUND` | 교환·반품·환불 신청 및 처리 현황 | 5. 교환·반품 접수, 6. 비용과 처리 |
| `OTHER` | 위 네 유형에 해당하지 않는 응대 범위 밖 문의 | (전용 장 없음 — 미분류 카테고리) |

다섯 라우트 모두에 공통으로 들어가는 장(`always_sections`): 이 매뉴얼을 쓰는 방법, 0. 상담 기본
원칙, 1. 문의 유형 분류, 7. 응대 범위와 이관, 8. 응대 태도와 문장.

분류 기준 자체는 매뉴얼 `### 1.1 분류 판단 기준`·`### 1.2 분류 우선순위`·`### 7.1 응대 범위 밖`에
글로 적혀 있고, 이 세 절의 발췌가 `domain.json`의 `routing_rules` 문자열로 옮겨져 라우터 프롬프트에
그대로 주입된다(`server/prompts.py:19`의 `build_route_guide`가 `{domain.routing_rules}`를 끼워
넣는다). 즉 매뉴얼 문서와 프롬프트가 별개로 관리되는 게 아니라, `routing_rules`가 매뉴얼 해당 절의
번역/발췌본이다.

미분류 카테고리 `OTHER`는 전용 장이 없고(`sections: []`), 여기로 분류된 문의는 `server/pipeline.py`의
`_node_escalate`가 처리한다 — `action == "OUT_OF_SCOPE"`면 `domain.out_of_scope_message`로 답하고
통화를 끊고, 그 외(`ESCALATE`, 확신도가 낮아 되묻지 않고 넘겨야 하는 경우)는 `domain.escalate_message`
로 상담원 이관을 안내한다.

**카테고리에 따라 서로 다른 근거만 프롬프트에 들어가게 구현한 부분**은 `server/context.py`의
`build_context(domain, route)`다. 매뉴얼 전문을 `## ` 헤딩 단위로 잘라(`split_sections`) 라우트별
`always_sections + routes[route].sections`만 이어붙인다. 전문을 다 넣지 않는 이유는 파일 상단 docstring에
있다 — 입력이 길어져 비용·지연이 늘고, 관련 없는 정책(예: 배송 문의에 반품 배송비가 섞여 들어가는 것)이
오답을 유도하기 때문이다.

이 매핑이 실제로 라우트마다 다른 컨텍스트를 만드는지는 LLM 호출 없이(매뉴얼 텍스트를 자르고 이어붙이는
것뿐이라 도구 호출이 필요 없다) 직접 실행해 확인할 수 있다. 아래는 그렇게 직접 실행해 얻은 출력이다
(`len`은 `build_context()`가 반환한 프롬프트 문자열의 글자 수):

```
ORDER_PLACE    len=6164  [쓰는 방법, 0, 1, 7, 8, 2. 주문·구매 문의]
PRODUCT_INFO   len=5674  [쓰는 방법, 0, 1, 7, 8, 3. 상품 문의]
SHIPPING       len=6559  [쓰는 방법, 0, 1, 7, 8, 4. 배송 문의]
RETURN_REFUND  len=7907  [쓰는 방법, 0, 1, 7, 8, 5. 교환·반품 접수, 6. 비용과 처리]
OTHER          len=4766  [쓰는 방법, 0, 1, 7, 8]                (전용 장 없음)
SHIPPING 에 6.1 반품배송비 포함? False   /   RETURN_REFUND 에 4.1 배송비 포함? False
```

재현 방법(저장소 루트에서, 키 불필요):

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
from server.domain import load_domain
from server.context import build_context

domain = load_domain(Path("domains/modumall"))
for route in domain.routes:
    ctx = build_context(domain, route)
    keys = domain.always_sections + domain.routes[route].sections
    print(f"{route:<14} len={len(ctx):<5} {keys}")

shipping_ctx = build_context(domain, "SHIPPING")
return_ctx = build_context(domain, "RETURN_REFUND")
print("SHIPPING 에 6.1 반품배송비 포함?", "### 6.1 반품 배송비" in shipping_ctx)
print("RETURN_REFUND 에 4.1 배송비 포함?", "### 4.1 배송비" in return_ctx)
PY
```

매뉴얼 9장(`### 9. 자주 발생하는 실수`)은 어떤 라우트의 `sections`에도, `always_sections`에도 들어
있지 않다 — 즉 현재는 어떤 프롬프트에도 주입되지 않는다. 의도적 설계인지 누락인지는 문서에 남아있지
않아 확인이 안 된다(`domains/modumall/domain.json`에 사실 그대로 주석을 남겨 두었다).

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

**대표 지표 (2026-09-17 최종 측정, `docs/측정기록/2026-09-17 3차 강화 측정.md`)**

| 지표 | 값 |
|---|---|
| 라우팅 정확도 | 0.933 (n=120) |
| 라우팅 macro F1 | 0.937 (n=120) |
| 확신도 보정 ECE | 0.046 (0에 가까울수록 확신도를 믿을 수 있음) |

라우팅은 정확도·macro F1·혼동행렬(`eval/eval_router.py`가 출력)로 측정한다. 답변 품질은 표 아래 기록처럼
여러 차례 바뀌었다 — 규칙 채점기 통과율이 최고치 50.0%(16/32), judge 를 더한 통과율 최고치는
62.5%(20/32, Task 4 시점)였다. 이후 진행 상태 정합성 작업(Task 5, 아래 표 행 5)에서 judge 포함 통과율이
53.1~56.2%로 낮아졌다. 이 수치는 표본이 **32건으로 작아** 1건 차이가 약 3.1%p를 움직이므로, 등락을
그대로 "성능이 나빠졌다/좋아졌다"로 읽기보다는 표본 크기의 한계를 감안해야 한다. 실패 사례를 하나하나
읽고 원인을 분류·기록한 문서는 `docs/측정기록/2026-09-17 진행상태 정합성 측정.md`에 있다 — 하락분을
A/B 재측정으로 좁혀 "프롬프트 규칙 5·6 때문이 아니라 `get_order_status` 도구가 모든 호출에
`active_process`/`events_note` 필드를 무조건 붙인 것(Task 2)이 유력한 원인"이라고 사례(C-016) 기반으로
판정했고, 설명되지 않는 실패(C-013·C-021)는 judge 채점 변동성(FLAP)으로 분류해 뭉뚱그리지 않았다.

**채점 기준 자체의 타당성도 확인해 두었다.**
- 규칙 채점기 자기검증(`eval/scoring.py`의 `self_check`): 모범 답안(reference)을 그대로 채점기에 넣어
  전부 통과하는지 본다. 하나라도 실패하면 채점기가 틀린 것이라는 뜻이다.
- judge 자기검증(`eval/judge.py:64-72`의 `judge_self_check`): 모범 답안을 답변으로, must 전부를
  누락 목록으로 넣어 judge 가 전부 통과시키는지 본다. 하나라도 실패하면 judge 프롬프트가 틀린 것이고,
  `eval_answer --judge` 실행 자체가 이 자기검증 실패 시 평가를 중단한다.
- 확신도 보정표·ECE(`eval/calibration.py`): 라우터가 말한 확신도와 실제 정확도가 구간별로 얼마나
  어긋나는지 계산해, "확신도가 높다고 해서 실제로 맞을 확률이 높은가"를 별도로 검증한다.

| # | 바꾼 것 | 라우팅 macro F1 | 답변 통과율 | 회귀 통과 | 메모 |
|---|---|---|---|---|---|
| 0 | 기준선 (규칙 라우터) | 0.677 | – | – | 정확도 0.583 |
| 1 | LLM 라우터 (gpt-4.1-mini) + 답변 gpt-4.1 | 0.945 | 43.8% (14/32, runs=3) | 6/6 PASS ×3회 | 정확도 0.933, ECE 0.039 · hard 72건 자동처리 0.611, 경계모호 15건 전부 확신 처리(위험) |
| 2 | 검색 동점 버그 수정 · 채점기 만 단위 정규화 · 도구 상한 4 · 주문번호 우선 조회 | 0.945 | 50.0% (16/32, runs=3, FLAP 2) | 6/6 | 남은 실패: 되물어야 할 때 답변 3건, must 문자열 의미 불일치 6건(품절/불가/925), 도구 미호출 6건 |
| 3 | A단계: SQLite 데이터(상품 180·주문 480) + 발신번호 고객 식별 + 마무리 인사 ASK 오판 수정 | 0.945 | (재측정 예정) | 6/6 | eval_context 40건: 자동화율 비회원 0.350 → 식별 고객 0.450, 되묻기율 0.525 → 0.400, 조회율 0.571 → 0.778 |
| 4 | 3차 강화: judge 채점기 · 필수 도구 표(부정 결과 → revert) · 확신도 마진 게이트(CONF_MARGIN=0.3) · followup 라우트 이어받기(FOLLOWUP_INHERIT, 기본 꺼짐) · 이관 도구 매핑 · 동의어 보강 | 0.937 | 규칙 50.0% / judge 포함 62.5% (20/32, runs=3, FLAP 2) | 6/6 ×3 | judge 판정기 효과 = 바뀌지 않은 에이전트에 judge 만 붙여 규칙 14/32 → judge 포함 21/32(65.6%). 라우트별 필수 도구 표 + 자기 점검 프롬프트는 부정 결과(규칙 13/32·표만 12/32, 기준선 14/32)라 revert. 경계모호 위험 15 → 10건(Task 4 격자 측정 시점 8건)으로 목표 5건 미달, 격자에 5건 이하 조합이 없음. 멀티턴 문맥 주입은 순효과 −1건(턴2+ 정확도 기준선 0.957 → 0.913)이라 이어받기 기본 꺼짐. 측정 원본: docs/측정기록/2026-09-17 3차 강화 측정.md |
| 5 | 진행 상태 정합성: 반품·교환 status_detail 을 반품 단계에서 파생 + 종결 상태(반품완료·교환완료) 신설 · get_order_status 에 active_process 필드 노출 · 답변 규칙 5·6(현재 상태 우선순위, 진행 중 사실 먼저 안내) · 가드레일 VIOLATION_STALE_STATE(SHIPPING·RETURN_REFUND 한정, 재시도해도 안 되면 통화 안 끊음) | 0.937 (동일) | 규칙 46.9% / judge 포함 **56.2%**(18/32, runs=3, FLAP 2) — 기준선 62.5% 대비 **하락 6.3%p** | 6/6 (동일) | 실통화 재현 O-1072 4/4 성공(반품 사실 우선 안내). 하락 원인을 가르려 규칙 5·6 만 뺀 **A/B 재측정을 완주**: judge 포함 **53.1%**(17/32, runs=3, FLAP 3) — 규칙을 빼도 회복되지 않음(오히려 −3.1%p, 표본 작아 오차 범위 가능) → **하락 원인은 규칙 5·6 이 아니라 Task 2(get_order_status active_process/events_note 필드)** 로 판정. 근거: C-016(정식 seed 주문, active_process=null)이 규칙 유무와 무관하게 두 실행 모두 동일 사유("7" 누락)로 실패, get_order_status 응답 비대화가 원인으로 추정. C-013·C-021 은 코드 변경으로 설명 안 돼 judge 채점 변동성(FLAP)으로 봄. eval_answer 는 Answerer 직접 호출이라 가드레일을 안 타므로 가드레일 효과는 이 수치에 안 잡힘(재현·회귀로만 확인). 측정 원본: docs/측정기록/2026-09-17 진행상태 정합성 측정.md |

## 고도화 내역

기본 과제(문의 분류 + 근거 기반 답변)를 넘어 구현한 것들이다.

| 항목 | 내용 |
|---|---|
| 가드레일 4종 + 재시도/이관 | `server/guardrail.py`가 답변을 4종으로 검사한다 — 출처 불명 수치(`VIOLATION_UNSOURCED`), 툴 미호출 단정(`VIOLATION_NO_TOOL`), 미확정값 확답(`VIOLATION_ASSERT_UNCONFIRMED`), 진행 중 상태 누락(`VIOLATION_STALE_STATE`, SHIPPING·RETURN_REFUND 한정). 위반 시 재시도하고, 그래도 안 되면 상담원 이관으로 뺀다. |
| 음성 STT·TTS + 한국어 발음 정규화 | 음성 인식은 Web Speech API, 합성은 브라우저 음성이 기본이고 `TTS_MODEL` 설정 시 OpenAI TTS 로 전환(`server/tts.py`). 숫자·영문 발음을 한국어 전화 통화에 맞게 정규화하는 `server/pronounce.py`가 있다. |
| 고객 마무리 발화 인식 | `server/pipeline.py`가 정규식 기반 결정적 규칙으로 고객의 마무리 신호를 판단해 SOFT(마무리 인사만 하고 통화 유지)와 HARD(통화 종료)를 구분한다. |
| 실시간 품질 패널 | `web/statspanel.js`가 통화 중 확신도(저확신 임계값)와 행동 판정 두 축을 누적 집계해 화면에 보여준다. |
| 어드민 콘솔 | `server/admin.py`/`server/adminrepo.py` + `web/dbpanel.js` 등이 `/admin/*`에 HTTP Basic 인증(fail-closed)으로 보호된 조회 화면을 제공한다. |
| 쇼핑몰 | `server/shop.py`가 전화번호 로그인·상품 구매를 제공하고, 주문이 실제 DB에 기록되어 그 고객으로 바로 통화를 걸어(발신번호 식별) 이어서 상담받을 수 있다. |
| BYOK 배포 | Render/Railway 배포에서 서버가 키를 들고 있지 않아도, 방문자가 자기 OpenAI 키를 넣어 통화할 수 있다(`server/llmkey.py`, `X-OpenAI-Key` 헤더). |
| 시연 데이터 20명 사연 설계 | `/shop` 로그인 화면 예시 고객과 통화 드롭다운에 결정적 사연을 가진 고객 20명을 배치해 시연 시나리오를 재현 가능하게 했다(최근 커밋 `cb94e5a`). |
| 테스트 499건 | `.venv/bin/pytest -q`로 키 없이 전부 통과 확인. |

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
