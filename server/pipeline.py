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
from server.context import section_titles
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

# ── 통화 종료 발화 판정 ──────────────────────────────────────────────
# "없습니다" 같은 짧은 대답을 후속 문의로 잘못 넘겨 같은 주제를 다시 설명하는 사고를 막는다.
# LLM 을 부르지 않고 규칙만으로 판정한다(비용 0, 응답 즉시). 판정은 두 단계다 — 무조건
# 끊는 게 능사가 아니다("알겠습니다"에 바로 전화를 끊으면 그것대로 무례하다):
#   SOFT: 마무리 인사만 하고 통화는 유지한다("없습니다" 류 — 대개 그 주제가 끝났다는
#         뜻이지 통화를 끝내겠다는 뜻은 아니다).
#   HARD: 작별 인사를 하고 통화를 끊는다("수고하세요" 류 — 명시적 작별만 여기 해당).
# 애매하면 아무 판정도 내리지 않는다(None) — 오판 비용(엉뚱하게 끊기거나, 엉뚱하게
# 마무리 인사를 하는 것)이 후속 문의를 한 번 더 처리하는 비용보다 훨씬 크다.
_ENDING_MAX_LEN = 15  # 선행 필러·공백을 정리한 뒤에도 이 길이를 넘으면 새 용건일 수 있어 보지 않는다.

# STT 결과는 구두점이 없고 띄어쓰기가 불규칙하다("네알겠습니다", "아 네 알겠습니다").
# 매칭 전에 공백으로 구분된 선행 필러 토큰(아/어/음/그/예/네/뭐, 반복 가능)만 제거한다 —
# "그렇군요"처럼 필러 글자로 시작하지만 뒤에 공백 없이 이어지는 단어를 잘못 깎아내지
# 않도록, 필러 뒤에 공백·쉼표·마침표가 실제로 있을 때만 지운다.
_ENDING_FILLER_PREFIX = re.compile(r"^(?:[아어음그예네뭐]+[,.\s]+)+")

# 상담원 답변이 "종결 질문"으로 끝났는지 — 되묻기·확인 질문과 구분해야 한다("사은품
# 있으셨나요?", "다른 번호 있으세요?", "하자가 있으실까요?" 뒤의 "없어요"는 종료가
# 아니라 되묻기 응답이다). 그래서 "있으실까요/있으신가요/있으세요/있으십니까"를 맨몸으로
# 넣지 않는다 — 그건 임의의 되묻기에도 다 걸린다. 종결 의미어(더/추가로/또/다른 +
# 궁금/문의/필요/도와, 또는 "더 도와드릴/언제든 말씀/편하게 말씀/다른 문의")와 함께 올
# 때만 종결 질문으로 본다.
_CLOSING_QUESTION_PATTERN = (
    r"(?:더|추가로|또|다른)\s*(?:궁금|문의|필요|도와)"
    r"|더\s*도와드릴|언제든\s*말씀|편하게\s*말씀|다른\s*문의"
)

# A군 — 부정·없음(SOFT). 직전 상담원 답변이 위 _CLOSING_QUESTION_PATTERN 에 걸렸을 때만
# 쓴다. 확인용 되묻기("사은품 있으셨나요?")에 대한 "없어요"는 이 군에 넣지 않는다 — 그건
# 종료가 아니라 되묻기 응답이라 기존 흐름(라우터·답변기) 그대로 가야 한다.
_ENDING_SOFT_NEGATIONS = (
    "없습니다", "없어요", "없네요", "없구요", "없는데요", "없을 것 같아요",
    "딱히 없어요", "특별히 없어요", "지금은 없어요", "더는 없어요", "그런 건 없어요",
    "아니요", "아뇨", "아니에요", "아닙니다",
    "괜찮아요", "괜찮습니다", "괜찮네요", "이제 괜찮아요",
    "됐어요", "됐습니다", "됐네요", "다 됐어요", "이제 됐어요", "그거면 됐어요",
    "충분해요", "충분합니다", "그거면 충분해요",
    "다 확인했어요", "다 들었어요",
)
# B군 — 수용·이해(SOFT). 직전 턴 내용과 무관하다. 단독 "감사합니다" 류는 통화 중간에
# 고마움만 표하고 말을 이어가는 경우가 흔해 여기(SOFT)에 둔다 — C군(작별)은 그 뒤에
# 명시적 작별어가 붙을 때만이다.
_ENDING_SOFT_ACCEPTANCES = (
    "알겠습니다", "알겠어요", "알겠네요", "네 알겠습니다", "아 네 알겠습니다",
    "그렇군요", "그렇구나", "그렇네요",
    "이해했습니다", "이해했어요", "잘 알겠습니다",
    "확인했습니다", "확인했어요",
    "감사합니다", "고맙습니다", "감사해요", "고마워요",
)
# C군 — 명시적 작별(HARD, 통화 종료). 직전 턴 내용과 무관하다. B군보다 먼저 검사해야
# "감사합니다 수고하세요"처럼 B군 낱말을 포함한 작별 인사가 SOFT 로 잘못 잡히지 않는다.
_ENDING_HARD_FAREWELLS = (
    "수고하세요", "수고하십시오", "수고하셨습니다",
    "고생하셨습니다", "고생 많으셨습니다", "애쓰셨습니다",
    "안녕히 계세요", "안녕히 계십시오", "들어가세요",
    "끊을게요", "끊겠습니다", "이만 끊을게요", "그럼 끊을게요",
    "이만 줄이겠습니다", "그럼 이만",
    "감사합니다 수고하세요", "고맙습니다 수고하세요", "네 감사합니다 안녕히 계세요",
)


# 부분 문자열 포함 매칭은 위험하다 — 목록 문구가 발화 어디에 박혀 있든 걸려버려서
# "언제 들어가세요"(정상 질문) 가 "들어가세요"(작별) 로, "안 괜찮아요"(불만) 가
# "괜찮아요"(수용) 로, "감사합니다 근데 하나만 더요"(새 용건) 가 "감사합니다"(수용)로
# 오판된다. 그래서 core 가 목록 문구와 "공백만 무시하고 완전히 같을 때"만 매칭한다 —
# 앞뒤로 다른 내용이 남아 있으면 매칭하지 않는다. 어미 변이(알겠어요/알겠네요 등)는
# 목록에 변이별로 다 적어 두는 방식으로 허용한다(정규식 형태소 분석 대신).
# 텍스트 입력창("말하기 대신 입력하려면...")으로 치는 발화는 "알겠습니다." 처럼 끝에
# 마침표를 붙이는 게 자연스럽다(음성 인식 결과엔 구두점이 거의 없지만). 완전 일치
# 매칭이라 이 부호 하나 때문에 못 잡으면(미탐) 안 되므로 끝에서부터 지운다. "?"는
# 절대 넣지 않는다 — 물음표는 classify_call_ending 이 먼저 별도로 걸러내는 가드라,
# 여기서 지워버리면 "들어가세요?" 같은 정상 질문이 다시 종료로 오판될 수 있다.
_TRAILING_PUNCT_CHARS = set(".!~…,·")


def _strip_trailing_punct(text: str) -> str:
    """끝에 붙은 문장부호(와 그 사이 공백)를 반복해서 지운다("알겠습니다 ..." 도 처리)."""
    t = text.strip()
    while t and (t[-1] in _TRAILING_PUNCT_CHARS or t[-1].isspace()):
        t = t[:-1]
    return t


def _ending_core(text: str) -> str:
    """공백만 정규화한 매칭용 문자열(필러 제거 없음)."""
    return re.sub(r"\s+", "", text.strip())


def _ending_core_defillered(text: str) -> str:
    """공백·쉼표로 구분된 선행 필러(아/어/음/그/예/네/뭐)를 지운 뒤 공백을 정규화한다.
    "그렇군요"처럼 필러 글자로 시작하지만 뒤에 구분자 없이 바로 이어지는 단어는 건드리지
    않는다(필러 뒤에 공백·쉼표·마침표가 실제로 있을 때만 지운다) — 그런 단어는 애초에
    _ending_core 단계에서 이미 목록과 완전히 같은지로 판정된다."""
    core = _ENDING_FILLER_PREFIX.sub("", text.strip())
    return re.sub(r"\s+", "", core)


def _matches_any(core: str, phrases: tuple[str, ...]) -> bool:
    """core 가 phrases 중 하나와 (공백 무시하고) 완전히 같은가 — 부분 포함이 아니다."""
    return core in {re.sub(r"\s+", "", p) for p in phrases}


def classify_call_ending(text: str, prev_answer: Optional[str]) -> Optional[str]:
    """고객의 현재 발화가 통화를 마무리하려는 신호인지 결정적 규칙으로 판정한다.

    text: 정규화된 고객 발화(이번 턴). prev_answer: 직전 상담원 답변(없으면 통화 첫
    발화 — 이때는 무조건 판정하지 않는다). LLM 없이 판정하므로 라우터·답변기는 아예
    호출하지 않는다. 반환값: "SOFT" | "HARD" | None(판정 없음 — 기존 흐름 그대로).
    """
    if not prev_answer:
        return None
    # 물음표 가드는 문장부호 정리보다 먼저 원문 그대로 봐야 한다 — "재입고 언제
    # 들어가세요?" 처럼 끝에 붙은 "?"가 정상 질문의 신호이기 때문이다("?"는 아래
    # _strip_trailing_punct 대상에서 뺐다).
    if "?" in text or re.search(ASK_PATTERN, text):
        return None
    text = _strip_trailing_punct(text)  # 텍스트 입력창은 "알겠습니다." 처럼 마침표를 붙인다
    is_closing_question = bool(re.search(_CLOSING_QUESTION_PATTERN, prev_answer))
    # 필러를 지우기 전(발화 그대로)과 지운 뒤, 두 형태 모두 "완전 일치"로만 본다.
    # 필러 제거본을 따로 두는 건 "음, 알겠습니다" 같은, 목록에 다 적어 두지 않은
    # 조합까지 잡기 위해서다 — "그렇군요"처럼 필러 글자로 시작하는 단어 자체는
    # 필러 제거 단계에서 안 건드리므로 첫 번째(원문) 패스에서 이미 그대로 걸린다.
    for core in (_ending_core(text), _ending_core_defillered(text)):
        if not core or len(core) > _ENDING_MAX_LEN:
            continue
        if _matches_any(core, _ENDING_HARD_FAREWELLS):
            return "HARD"
        if _matches_any(core, _ENDING_SOFT_ACCEPTANCES):
            return "SOFT"
        if is_closing_question and _matches_any(core, _ENDING_SOFT_NEGATIONS):
            return "SOFT"
    return None


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
    evidence: Optional[dict]  # {"always": [...], "route": [...]} — 답변이 실제로 근거한 매뉴얼 장 제목


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
    evidence: Optional[dict] = None  # {"always": [...], "route": [...]} — None 이면 근거 문서를 쓰지 않은 턴

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
                "attempts": 0, "tools": [], "results": {}, "guardrail": None,
                # 매 턴 시작마다 초기화한다 — 체크포인터가 통화(call_id) 동안 state 를 이어가므로,
                # 초기화하지 않으면 이전 턴에 답변이 근거로 쓴 장이 이번 턴(예: ASK/ESCALATE 로 끝나
                # 실제로는 매뉴얼을 쓰지 않은 턴)에도 그대로 남아 보일 수 있다.
                "evidence": None}
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
        # 답변기가 이 턴에 실제로 쓴 컨텍스트는 build_context(domain, state["route"]) 그대로다
        # (server/answer.py 의 build_answer_prompt 가 같은 route 로 호출한다) — 여기서
        # section_titles 로 그 값을 사람이 읽을 장 제목으로만 바꿔서 꺼내 쓴다(섹션을 다시
        # 자르거나 라우트에서 새로 추론하지 않는다).
        evidence = section_titles(self.domain, state["route"])
        return {"action": "HANDLE", "tools": calls, "results": results, "answer": text, "attempts": attempts,
                "evidence": evidence}

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
        # 이관·범위 밖 문구는 매뉴얼 장을 근거로 하지 않는다. 재시도 소진으로 answer 노드를
        # 거친 뒤 여기로 넘어온 경우 이전 시도의 evidence 가 state 에 남아 있을 수 있어 지운다.
        if state["action"] == "OUT_OF_SCOPE":
            return {"answer": self.domain.out_of_scope_message, "history": [state["question"]], "evidence": None}
        return {"answer": self.domain.escalate_message, "action": "ESCALATE",
                "history": [state["question"]], "evidence": None}

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

    # 드롭다운 한 줄이 터지지 않도록 힌트에 보여줄 상태 종류 수 상한(넘으면 나머지는 생략).
    _HINT_STATUS_CAP = 3

    def _sample_entry(self, c: dict) -> dict:
        # 최근 N건이 아니라 그 고객의 "진행 중 주문 전체"를 본다 — 진행 중 주문이 4번째 이후로
        # 밀려도(다른 완료된 주문이 더 최근이어도) 힌트에서 빠지면 안 된다.
        active = [o["status"] for o in self.repo.in_progress_orders(c["customer_id"])]
        statuses = list(dict.fromkeys(active))[:self._HINT_STATUS_CAP]
        return {"name": c["name"], "phone": c["phone"], "hint": "·".join(statuses) or None}

    def sample_customers(self, n: int = 20) -> list[dict]:
        """통화 화면 드롭다운에 쓸 고객 전체 목록(기본값 n=20 은 도메인 고객 총원과 같다 — 쇼핑몰에서
        방금 주문한 고객도 반드시 이 목록에 들어오게 하려면 일부만 추리지 않고 전원을 돌려줘야 한다).
        다만 정렬은 유지한다: 진행 중 상태가 다양한 고객이 위에 오도록 먼저 뽑고(배송중 → 반품 →
        교환 → 지연 → 제작 → 결제완료), 나머지 고객은 뒤에 이어 붙인다 — 드롭다운을 위에서부터
        훑으면 상태 다양성이 먼저 보인다."""
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
        # 위 단계로도 못 채운 나머지는 고객 ID 순으로 전부 붙인다 (쇼핑몰에서 막 주문한 고객이라도
        # 반드시 목록에 들어오게 하는 최종 안전망).
        for cid in self.repo.all_customer_ids():
            if len(out) >= n:
                break
            if cid in seen:
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
        prev_turns = self.turn_logs.get(call_id) or []
        prev_answer = prev_turns[-1]["a"] if prev_turns else None
        verdict = classify_call_ending(text, prev_answer)
        if verdict:
            # SOFT/HARD 둘 다 라우터·답변기·가드레일을 전부 건너뛴다(LLM 호출 0회).
            # SOFT 는 마무리 인사만 하고 통화를 유지하고(end_call=False), HARD 만 끊는다.
            hard = verdict == "HARD"
            answer = self.domain.closing_message if hard else self.domain.soft_closing_message
            self.turn_logs.setdefault(call_id, []).append({
                "q": text, "a": answer, "route": None, "confidence": None, "action": "ANSWER",
                "tools": [], "guardrail_ok": None, "violations": [], "followup": False,
                "end_call": hard, "evidence": None,  # 통화 종료 판정은 매뉴얼을 조회하지 않는다
            })
            return TurnResult(answer=answer, route=None, confidence=None, action="ANSWER", tools=[],
                              guardrail=None, elapsed_ms=int((time.perf_counter() - t0) * 1000),
                              end_call=hard, attempts=0, evidence=None)
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
            # 브라우저 통화 누적 통계 패널(web/statspanel.js)이 안전 축(위반 유형별 집계)을
            # 계산하려면 위반 내역이 필요하다. 기존 guardrail_ok(불리언)만으로는 "무엇이"
            # 위반인지 알 수 없어 최소 추가한다. 화면 소비자(server/templates/call_detail.html)는
            # guardrail_ok만 읽으므로 영향 없다.
            "violations": g.get("violations", []) if g else [],
            "followup": out.get("is_followup", False),
            "evidence": out.get("evidence"),
        })
        return TurnResult(answer=out["answer"], route=out.get("route"), confidence=out.get("confidence"),
                          action=action, tools=out.get("tools") or [], guardrail=out.get("guardrail"),
                          elapsed_ms=int((time.perf_counter() - t0) * 1000), end_call=end,
                          attempts=out.get("attempts", 0),
                          route_alt=out.get("route_alt"), alt_confidence=out.get("alt_confidence"),
                          is_followup=out.get("is_followup", False), evidence=out.get("evidence"))
