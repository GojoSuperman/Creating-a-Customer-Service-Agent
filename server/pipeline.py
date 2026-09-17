# -*- coding: utf-8 -*-
"""라우터 · 조회 · 생성 · 가드레일을 하나의 그래프로 잇는다.

route → answer → guard → (END | answer 재시도 | escalate)
통화(call_id)마다 체크포인터가 State 를 보존하므로 앞 턴의 발화가 뒤 턴에 이어진다.
"""
import contextvars
import datetime
import hashlib
import operator
import re
import time
import uuid
from dataclasses import dataclass, asdict
from typing import Annotated, Optional, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from server import guardrail
from server.callcontext import current_caller
from server.config import Settings
from server.domain import Domain
from server.lrucache import LRUCache
from server.repo import Repo

# 방문자마다 다른 OpenAI 키(X-OpenAI-Key)로 라우터·답변기 인스턴스를 만들어 캐싱한다.
# 공개 URL 에서 헤더만 바꿔 반복 호출하면 키 개수만큼 무제한으로 자라 OOM 이 나므로
# 상한을 두고 넘치면 오래 쓰이지 않은 것부터 버린다(server/lrucache.py 참고).
_KEY_CACHE_MAXSIZE = 32


def _key_hash(api_key: str) -> str:
    """캐시에 원본 키 대신 해시를 남긴다(캐시 자체가 들여다볼 수 있는 표면이라)."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()

# 데이터 생성기가 결정적으로 오늘 날짜를 이 값으로 고정해 두었다 (server/db/generate.py 참고).
# 통화 인사말에서 "최근 14일 내 주문" 여부를 실측 시각과 무관하게 재현 가능하도록 상수로 둔다.
TODAY = datetime.date(2026, 9, 16)

# 이 턴에서 쓸 라우터·답변기 인스턴스(요청별 OpenAI 키로 만들어진 것일 수 있다).
# AgentState 에 넣지 않는 이유: InMemorySaver 가 체크포인트마다 state 를 msgpack 으로
# 직렬화하는데, 컴파일된 그래프·모델 인스턴스는 직렬화할 수 없어 매 턴 TypeError 가 난다.
# current_caller 와 같은 이유로 contextvars 를 쓴다 — Pipeline.turn 이 그래프를 호출하는
# 동안만 값을 채우고 끝나면 되돌린다.
current_router: contextvars.ContextVar[Optional[object]] = contextvars.ContextVar("current_router", default=None)
current_answerer: contextvars.ContextVar[Optional[object]] = contextvars.ContextVar("current_answerer", default=None)

ASK_PATTERN = r"\?|주시겠|알려주|말씀해"
# 답변 끝의 상담 종결 인사("더 궁금한 점 있으시면 말씀해 주세요")는 질문형 어미를 갖고 있어도
# ASK 로 오판되면 안 된다. 다만 "추가로 필요한 사이즈를 말씀해 주시겠어요?"처럼 실제 질문에도
# 같은 낱말(추가로/필요)이 섞여 있을 수 있어, 문장 전체를 지우는 대신 마지막 문장이 "?" 로
# 끝나지 않으면서 이 패턴에 걸릴 때만 그 문장 하나를 판정에서 제외한다.
CLOSING_PATTERN = r"(?:더|추가로|또)\s*(?:궁금|문의|필요)|언제든|편하게\s*말씀"


def called_escalate(results: dict) -> bool:
    """답변기가 escalate_to_agent 도구를 실제로 불렀는가. 오류 문자열 값은 호출로 치지 않는다."""
    for key, value in (results or {}).items():
        if key.split("#")[0] == "escalate_to_agent" and isinstance(value, dict) and value.get("escalated"):
            return True
    return False


def infer_action(text: str, results: dict) -> str:
    """평가 채점기와 런타임이 같은 판정을 쓴다.

    반환값: "OUT_OF_SCOPE" (외부 채널) / "ASK" (질문 패턴) / "ANSWER" (기본값)

    tools 결과는 오류 문자열일 수도 있고(모델이 잘못된 kwargs 를 넘기면 ToolNode 가
    오류 텍스트를 저장한다), 같은 도구가 한 턴에 여러 번 불리면 `get_order_status#2`,
    `#3` 같은 키로도 들어온다. 기본 이름이 `get_order_status` 인 모든 키를 훑되,
    dict 값만 검사한다.
    """
    for key, value in (results or {}).items():
        if key.split("#")[0] != "get_order_status":
            continue
        if isinstance(value, dict) and value.get("is_external_channel"):
            return "OUT_OF_SCOPE"
    if not results:
        sentences = re.split(r"(?<=[.!?])\s+", text.strip())
        check_text = text
        if sentences:
            last = sentences[-1]
            if not last.rstrip().endswith("?") and re.search(CLOSING_PATTERN, last):
                check_text = " ".join(sentences[:-1])
        if re.search(ASK_PATTERN, check_text):
            return "ASK"
    return "ANSWER"


_KO_DIGIT = {"공": "0", "영": "0", "일": "1", "이": "2", "삼": "3", "사": "4",
             "오": "5", "육": "6", "륙": "6", "칠": "7", "팔": "8", "구": "9"}
_PREFIX_WORDS = {"제로": "O", "영": "O", "공": "O", "오": "O", "오우": "O", "o": "O", "O": "O", "0": "O",
                 "알": "R", "아르": "R", "r": "R", "R": "R", "피": "P", "p": "P", "P": "P"}
_SEP = r"(?:\s*(?:다시|대시|대쉬|하이픈|빼기|-)\s*)"


def normalize_stt(text: str) -> str:
    """음성 인식 결과에서 식별자를 복원한다.

    "제로 다시 1006" / "영 다시 일공공육" / "0-1006" / "o-1006" → "O-1006",
    "알 다시 2001" → "R-2001", "1001번 주문" → "O-1001번 주문".
    인식기가 숫자를 잘라 버린 경우(1006→100)는 복구할 수 없다.
    """
    # 1) 한글로 읽힌 숫자 묶음 (3자리 이상만 — "이 상품" 같은 일상어 오변환 방지)
    text = re.sub(r"(?<![가-힣])[공영일이삼사오육륙칠팔구]{3,4}(?![가-힣])",
                  lambda m: "".join(_KO_DIGIT[c] for c in m.group(0)), text)
    # 2) "<접두어> 다시 <숫자>" → "<O|R|P>-<숫자>"
    words = "|".join(sorted(map(re.escape, _PREFIX_WORDS), key=len, reverse=True))
    text = re.sub(rf"(?<![가-힣\w])({words}){_SEP}(\d{{3,4}})\b",
                  lambda m: f"{_PREFIX_WORDS[m.group(1)]}-{m.group(2)}", text)
    # 3) 접두어 없이 "1001번 주문"처럼 말한 4자리 숫자 → 주문번호
    text = re.sub(r"(?<![\w-])(\d{4})(\s*번)?(\s*주문)", r"O-\1\2\3", text)
    return text


class AgentState(TypedDict, total=False):
    question: str
    history: Annotated[list, operator.add]   # 리듀서: 턴마다 쌓인다
    route: str
    confidence: float
    route_alt: Optional[str]
    alt_confidence: float
    # 리듀서: 턴마다 라우팅 판단 한 건({route, confidence, is_followup, gated})이 쌓인다
    routes: Annotated[list, operator.add]
    is_followup: bool
    action: str        # HANDLE / ASK / ANSWER / RETRY / ESCALATE / OUT_OF_SCOPE
    tools: list
    results: dict
    answer: str
    guardrail: Optional[dict]
    attempts: int
    clarify_count: int
    customer: Optional[dict]
    call_id: str


@dataclass
class TurnResult:
    answer: str
    route: Optional[str]
    confidence: Optional[float]
    action: str
    tools: list
    guardrail: Optional[dict]
    elapsed_ms: int
    end_call: bool
    attempts: int
    route_alt: Optional[str] = None
    alt_confidence: Optional[float] = None
    is_followup: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def compose_router_input(question: str, history: list, prev_route: Optional[str]) -> str:
    """라우터 입력 문자열. 현재 문의를 앞에 두고 최근 두 발화와 직전 라우트를 뒤에 붙인다.
    (긴 통화일수록 앞 turn 이 라우팅을 오염시키는 걸 줄이기 위해 두 발화만 본다.)
    eval_router --multiturn 이 같은 함수를 써서 평가와 런타임의 입력이 같다."""
    q = question
    hist = (history or [])[-2:]
    if hist:
        q += f"\n[직전 문의] {' / '.join(hist)}"
    if prev_route:
        q += f"\n[직전 라우트] {prev_route}"
    return q


def should_inherit(is_followup: bool, enabled: bool, prev_route, prev_gated: bool) -> bool:
    """후속 발화가 직전 라우트를 이어받을지. 옵션이 켜져 있고, 직전 턴이 있고, 그 턴이 게이트에 걸리지 않았고, OTHER 가 아닐 때만."""
    return bool(is_followup) and enabled and prev_route is not None and not prev_gated and prev_route != "OTHER"


class Pipeline:
    def __init__(self, domain: Domain, settings: Settings, router=None, answerer=None):
        self.domain = domain
        self.settings = settings
        # router/answerer 를 밖에서 주입받았으면(테스트의 가짜 객체 등) 항상 그것만 쓴다 — 요청별
        # 키가 와도 무시한다. 주입받지 않았으면 기본은 서버 환경변수 폴백으로 미리 만들어 두고,
        # 요청에 자기 키가 실려 오면 _router_for/_answerer_for 가 그 키 전용 인스턴스를 따로
        # 만들어 캐싱한다(동시 요청 간 키가 섞이지 않도록 전역 상태를 바꾸지 않는다).
        self._router_injected = router is not None
        self._answerer_injected = answerer is not None
        if router is None:
            from server.router import build_router
            router = build_router(domain, settings.conf_threshold, model=settings.router_model,
                                  conf_margin=settings.conf_margin)
        if answerer is None:
            from server.answer import Answerer
            answerer = Answerer(domain, model=settings.answer_model, max_tool_turns=settings.max_tool_turns)
        self.router = router
        self.answerer = answerer
        self._router_cache: LRUCache = LRUCache(maxsize=_KEY_CACHE_MAXSIZE)
        self._answerer_cache: LRUCache = LRUCache(maxsize=_KEY_CACHE_MAXSIZE)
        self.repo = Repo(domain.db_path)
        self.calls: set[str] = set()
        self.customers: dict[str, Optional[dict]] = {}
        self.turn_logs: dict[str, list] = {}
        self.graph = self._build()

    def _router_for(self, api_key: Optional[str]):
        if self._router_injected or not api_key:
            return self.router

        def factory():
            from server.router import build_router
            return build_router(self.domain, self.settings.conf_threshold, model=self.settings.router_model,
                                conf_margin=self.settings.conf_margin, api_key=api_key)

        return self._router_cache.get_or_create(_key_hash(api_key), factory)

    def _answerer_for(self, api_key: Optional[str]):
        if self._answerer_injected or not api_key:
            return self.answerer

        def factory():
            from server.answer import Answerer
            return Answerer(self.domain, model=self.settings.answer_model,
                            max_tool_turns=self.settings.max_tool_turns, api_key=api_key)

        a = self._answerer_cache.get_or_create(_key_hash(api_key), factory)
        return a

    # ── 노드 ──────────────────────────────────────────────
    def _node_route(self, state: AgentState) -> AgentState:
        router = current_router.get() or self.router
        routes = state.get("routes") or []
        prev_entry = routes[-1] if routes else None
        prev = prev_entry["route"] if prev_entry else None
        q = compose_router_input(state["question"], state.get("history") or [], prev)
        r = router.invoke({"question": q})
        route = r["route"]
        gated = r["action"] == "ESCALATE"   # 게이트(확신도·마진)가 이 턴의 판단을 거부했다
        is_followup = bool(r.get("is_followup"))   # 라우터가 낸 원값. 기록·관찰용으로 그대로 남긴다
        # 라우트 강제 이어받기는 FOLLOWUP_INHERIT 옵션(기본 꺼짐)일 때만 한다 — 멀티턴 측정(2026-09-17)에서
        # 강제 상속이 라우트 전환 발화를 망쳐 순손실이었다. 켠 경우에도 게이트 미달 턴의 라우트는 추측이므로
        # 상속 앵커로 쓰지 않는다([직전 라우트] 는 모델에 계속 알려 주고 이어받기만 막는다). OTHER 도 제외.
        followup = should_inherit(is_followup, self.settings.followup_inherit, prev,
                                  bool(prev_entry and prev_entry["gated"]))
        if followup:
            route = prev   # 후속 발화는 라우트만 이어받고, 확신도 판정(action)은 라우터 결과를 그대로 쓴다
        base = {"route": route, "confidence": r["confidence"], "action": r["action"],
                "route_alt": r.get("route_alt"), "alt_confidence": r.get("alt_confidence", 0.0),
                "is_followup": is_followup,
                "routes": [{"route": route, "confidence": r["confidence"],
                            "is_followup": is_followup, "gated": gated}],
                "attempts": 0, "tools": [], "results": {}, "guardrail": None}
        count = state.get("clarify_count", 0)
        if r["action"] == "ESCALATE" and count < self.settings.clarify_max:
            # 확신도 미달을 곧바로 이관하지 않고 한 번 되묻는다 (cs-chatbot-design 의 2단계 fallback)
            base.update({"action": "ASK", "answer": self.domain.clarify_message,
                         "clarify_count": count + 1, "history": [state["question"]]})
        return base

    def _node_answer(self, state: AgentState) -> AgentState:
        answerer = current_answerer.get() or self.answerer
        guardrail_state = state.get("guardrail")
        feedback = None
        if guardrail_state and not guardrail_state.get("ok"):
            feedback = "; ".join(f"{v['type']}: {v['detail']}" for v in guardrail_state["violations"])
        customer = state.get("customer")
        text, results, calls = answerer.answer(state["question"], state["route"],
                                              history=state.get("history") or [],
                                              feedback=feedback, customer=customer)
        attempts = state.get("attempts", 0) + 1
        if text == self.domain.escalate_message:
            # 답변기가 도구 호출 상한에 걸려 스스로 이관 문구를 돌려준 경우 — 일반 답변으로 흘리지 않는다
            return {"action": "ESCALATE", "tools": calls, "results": results, "attempts": attempts}
        if called_escalate(results):
            # 모델이 이관 도구를 불렀으면 답변 문구와 무관하게 이관한다 (매뉴얼 7.2)
            return {"action": "ESCALATE", "tools": calls, "results": results, "attempts": attempts}
        action_inferred = infer_action(text, results)
        if action_inferred == "OUT_OF_SCOPE":
            return {"action": "OUT_OF_SCOPE", "tools": calls, "results": results, "attempts": attempts}
        if action_inferred == "ASK":
            return {"action": "ASK", "tools": calls, "results": {}, "answer": text, "attempts": attempts,
                    "history": [state["question"]], "guardrail": None}
        # action_inferred == "ANSWER" → internal "HANDLE" action
        return {"action": "HANDLE", "tools": calls, "results": results, "answer": text, "attempts": attempts}

    def _node_guard(self, state: AgentState) -> AgentState:
        customer = state.get("customer")
        results = dict(state["results"])
        if customer:
            results["_customer"] = customer
        g = guardrail.check(state["answer"], results, self.domain, route=state["route"])
        if not g.ok:
            for v in g.violations:
                guardrail.log_violation(self.settings.logs_dir, {
                    "call_id": state.get("call_id"), "route": state["route"], "type": v["type"],
                    "detail": v["detail"], "answer": state["answer"]})
        # 재시도가 소진됐는데 남은 위반이 전부 "진행 중 상태 누락"뿐이면 이관하지 않는다. 이 위반은
        # "틀린 값을 말했다"가 아니라 "말을 덜 했다"이므로, 통화를 끊고 이관 문구로 바꾸는 대가가
        # 과하다 — 경고만 남기고 이미 생성된 답변을 그대로 내보낸다. (LangGraph 의 분기 함수는
        # state 를 고쳐 쓸 수 없어, action/history 를 여기서 확정해야 _after_guard 가 그대로
        # END 로 보낸다.)
        retries_exhausted = state.get("attempts", 0) > self.settings.guardrail_retry
        only_stale = bool(g.violations) and all(v["type"] == guardrail.VIOLATION_STALE_STATE
                                                for v in g.violations)
        if not g.ok and retries_exhausted and only_stale:
            guardrail.log_violation(self.settings.logs_dir, {
                "call_id": state.get("call_id"), "route": state["route"],
                "type": "STALE_STATE_PASSTHROUGH",
                "detail": "재시도 소진, 진행 중 상태 누락만 남아 이관 없이 답변을 그대로 내보냄",
                "answer": state["answer"]})
            return {"guardrail": g.to_dict(), "action": "ANSWER", "history": [state["question"]]}
        result = {"guardrail": g.to_dict(), "action": "ANSWER" if g.ok else "RETRY"}
        if g.ok:
            result["history"] = [state["question"]]
        return result

    def _node_escalate(self, state: AgentState) -> AgentState:
        if state["action"] == "OUT_OF_SCOPE":
            return {"answer": self.domain.out_of_scope_message, "history": [state["question"]]}
        return {"answer": self.domain.escalate_message, "action": "ESCALATE",
                "history": [state["question"]]}

    # ── 분기 ──────────────────────────────────────────────
    def _after_route(self, state: AgentState) -> str:
        if state["action"] == "HANDLE":
            return "answer"
        return END if state["action"] == "ASK" else "escalate"

    def _after_answer(self, state: AgentState) -> str:
        if state["action"] == "ASK":
            return END
        if state["action"] in ("OUT_OF_SCOPE", "ESCALATE"):
            return "escalate"
        return "guard"

    def _after_guard(self, state: AgentState) -> str:
        if state["action"] == "ANSWER":
            return END
        return "answer" if state["attempts"] <= self.settings.guardrail_retry else "escalate"

    def _build(self):
        g = StateGraph(AgentState)
        g.add_node("route", self._node_route)
        g.add_node("answer", self._node_answer)
        g.add_node("guard", self._node_guard)
        g.add_node("escalate", self._node_escalate)
        g.add_edge(START, "route")
        g.add_conditional_edges("route", self._after_route, {"answer": "answer", "escalate": "escalate", END: END})
        g.add_conditional_edges("answer", self._after_answer, {"guard": "guard", "escalate": "escalate", END: END})
        g.add_conditional_edges("guard", self._after_guard, {"answer": "answer", "escalate": "escalate", END: END})
        g.add_edge("escalate", END)
        return g.compile(checkpointer=InMemorySaver())

    # ── 공개 API ──────────────────────────────────────────
    def _greeting_for(self, customer: Optional[dict]) -> str:
        if not customer:
            return self.domain.greeting
        greeting = self.domain.greeting_known.format(name=customer["name"], shop=self.domain.name)
        for o in customer.get("recent_orders") or []:
            ordered_at = o.get("ordered_at")
            if not ordered_at:
                continue
            ordered_date = datetime.date.fromisoformat(ordered_at[:10])
            days = (TODAY - ordered_date).days
            if 0 <= days <= 14 and o.get("status") not in ("배송완료", "반품완료", "교환완료"):
                month, day = int(ordered_at[5:7]), int(ordered_at[8:10])
                greeting += f" {month}월 {day}일 주문하신 {o.get('items_summary', '')} 건이신가요?"
                break
        return greeting

    def start_call(self, phone: Optional[str] = None) -> tuple[str, str, Optional[dict]]:
        cid = uuid.uuid4().hex[:12]
        self.calls.add(cid)
        self.turn_logs[cid] = []
        customer = None
        if phone:
            c = self.repo.customer_by_phone(phone)
            if c:
                customer = {"customer_id": c["customer_id"], "name": c["name"], "phone": c["phone"],
                            "recent_orders": self.repo.recent_orders(c["customer_id"], 3)}
        self.customers[cid] = customer
        self.repo.log_call(cid, customer["customer_id"] if customer else None,
                           datetime.datetime.now().isoformat())
        return cid, self._greeting_for(customer), customer

    def customer_profile(self, customer_id: str) -> Optional[dict]:
        """상담원 패널 전용. 주소·전화 등을 포함하므로 답변 프롬프트(self.customers)와는 분리해 둔다."""
        return self.repo.customer_profile(customer_id)

    def _sample_entry(self, c: dict) -> dict:
        active = [o["status"] for o in self.repo.recent_orders(c["customer_id"], 3) if o["status"] not in self.repo.IN_PROGRESS_EXCLUDED]
        return {"name": c["name"], "phone": c["phone"], "hint": "·".join(dict.fromkeys(active)) or None}

    def sample_customers(self, n: int = 8) -> list[dict]:
        """최근 30일 내 미배송 완료 주문이 있는 고객을 우선 추천한다 (통화 데모가 실제 진행 중인
        주문을 보여줄 수 있도록). 부족하면 정식 주문 O-1001..O-1005 소유자로 채운다."""
        seen: set[str] = set()
        out: list[dict] = []
        cutoff = (TODAY - datetime.timedelta(days=30)).isoformat()
        # 테스트 다양성: 진행 중 상태별로 한 명씩 먼저 뽑는다 (배송중 → 반품 → 교환 → 지연 → 제작 → 결제완료)
        for status in ("배송중", "반품진행", "교환진행", "배송지연", "제작중", "결제완료"):
            row = self.repo.latest_customer_by_status(status, TODAY.isoformat(), cutoff)
            if not row or row["customer_id"] in seen:
                continue
            seen.add(row["customer_id"])
            c = self.repo.customer(row["customer_id"])
            if c:
                out.append(self._sample_entry(c))
        for row in self.repo.recently_active_customers(TODAY.isoformat(), cutoff, n + len(seen)):
            cid = row["customer_id"]
            if cid in seen:
                continue
            seen.add(cid)
            c = self.repo.customer(cid)
            if c:
                out.append(self._sample_entry(c))
            if len(out) >= n:
                return out
        for i in range(1, n + 1):
            if len(out) >= n:
                break
            o = self.repo.order(f"O-{1000 + i}")
            cid = o.get("customer_id") if o else None
            if not cid or cid in seen:
                continue
            seen.add(cid)
            c = self.repo.customer(cid)
            if c:
                out.append(self._sample_entry(c))
        return out

    def end_call(self, call_id: str) -> None:
        turns = self.turn_logs.pop(call_id, [])
        self.repo.finish_call(call_id, datetime.datetime.now().isoformat(), turns)

    def turn(self, call_id: str, text: str, api_key: Optional[str] = None) -> TurnResult:
        """api_key 가 주어지면 이 턴에서만 그 키로 만든 라우터·답변기를 쓴다(다른 통화·다른
        사용자와 섞이지 않는다). 없으면 서버 환경변수 폴백(생성자에서 이미 만들어 둔
        self.router/self.answerer)을 그대로 쓴다."""
        if call_id not in self.calls:
            raise KeyError(call_id)
        t0 = time.perf_counter()
        text = normalize_stt(text)
        cfg = {"configurable": {"thread_id": call_id}}
        customer = self.customers.get(call_id)
        caller = None
        if customer:
            caller = {"customer_id": customer["customer_id"], "phone": customer.get("phone")}
        router = self._router_for(api_key)
        answerer = self._answerer_for(api_key)
        caller_token = current_caller.set(caller)
        router_token = current_router.set(router)
        answerer_token = current_answerer.set(answerer)
        try:
            out = self.graph.invoke({"question": text, "customer": customer, "call_id": call_id}, cfg)
        finally:
            current_caller.reset(caller_token)
            current_router.reset(router_token)
            current_answerer.reset(answerer_token)
        action = out["action"]
        end = action in ("ESCALATE", "OUT_OF_SCOPE")
        g = out.get("guardrail")
        self.turn_logs.setdefault(call_id, []).append({
            "q": text, "a": out["answer"], "route": out.get("route"),
            "confidence": out.get("confidence"), "action": action,
            "tools": [t["name"] for t in (out.get("tools") or [])],
            "guardrail_ok": g.get("ok") if g else None,
            # 실시간 턴 판정(server/turnscore.py machine_flags)이 안전 축을 계산하려면 위반
            # 내역이 필요하다. 기존 guardrail_ok(불리언)만으로는 "무엇이" 위반인지 알 수 없어
            # 최소 추가한다. 화면 소비자(server/templates/call_detail.html)는 guardrail_ok만
            # 읽으므로 영향 없다.
            "violations": g.get("violations", []) if g else [],
            "followup": out.get("is_followup", False),
        })
        return TurnResult(answer=out["answer"], route=out.get("route"), confidence=out.get("confidence"),
                          action=action, tools=out.get("tools") or [], guardrail=out.get("guardrail"),
                          elapsed_ms=int((time.perf_counter() - t0) * 1000), end_call=end,
                          attempts=out.get("attempts", 0),
                          route_alt=out.get("route_alt"), alt_confidence=out.get("alt_confidence"),
                          is_followup=out.get("is_followup", False))
