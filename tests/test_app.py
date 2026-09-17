import pytest
from fastapi.testclient import TestClient
from server.app import create_app
from server.domain import load_domain
from server.pipeline import TurnResult


class FakePipeline:
    def __init__(self):
        self.calls = set()
        self.ended = []
        self.turn_api_keys = []   # 어떤 api_key 로 turn() 이 불렸는지 기록(테스트 검증용)

    def start_call(self, phone=None):
        self.calls.add("abc")
        return "abc", "안녕하세요", None

    def end_call(self, call_id):
        self.ended.append(call_id)

    def turn(self, call_id, text, api_key=None):
        if call_id not in self.calls:
            raise KeyError(call_id)
        self.turn_api_keys.append(api_key)
        return TurnResult(answer=f"응답:{text}", route="SHIPPING", confidence=0.9, action="ANSWER",
                          tools=[], guardrail={"ok": True, "violations": []}, elapsed_ms=5, end_call=False,
                          attempts=1)


class BrokenPipeline(FakePipeline):
    def turn(self, call_id, text, api_key=None):
        raise RuntimeError("LLM 호출 실패")


class KeyLeakPipeline(FakePipeline):
    """예외 메시지에 키 원문이 섞여 나오는 상황을 흉내낸다(예: OpenAI 인증 오류가 키 일부를
    되돌려주는 경우). 전역 예외 핸들러가 이걸 가려야 한다."""
    def turn(self, call_id, text, api_key=None):
        raise RuntimeError(f"인증 실패: Incorrect API key provided: {api_key}")


@pytest.fixture
def client(modumall_dir):
    return TestClient(create_app(FakePipeline(), load_domain(modumall_dir)))


HEADERS_WITH_KEY = {"X-OpenAI-Key": "sk-test-abcdef1234567890ABCDEF3f9a"}


def test_domain_endpoint(client):
    r = client.get("/api/domain")
    assert r.status_code == 200 and r.json()["name"] == "모두몰" and "greeting" in r.json()


def test_start_and_turn(client):
    s = client.post("/api/call/start").json()
    assert s["call_id"] == "abc" and s["greeting"]
    t = client.post("/api/call/turn", json={"call_id": "abc", "text": "배송비"}, headers=HEADERS_WITH_KEY).json()
    assert t["answer"] == "응답:배송비" and t["action"] == "ANSWER" and t["end_call"] is False


def test_unknown_call_is_404(client):
    r = client.post("/api/call/turn", json={"call_id": "zzz", "text": "x"}, headers=HEADERS_WITH_KEY)
    assert r.status_code == 404


def test_empty_text_is_422(client):
    r = client.post("/api/call/turn", json={"call_id": "abc", "text": "  "}, headers=HEADERS_WITH_KEY)
    assert r.status_code == 422


def test_turn_without_key_and_without_env_fallback_is_401(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client.post("/api/call/start")
    r = client.post("/api/call/turn", json={"call_id": "abc", "text": "배송비"})
    assert r.status_code == 401
    assert "OpenAI" in r.json()["detail"]


def test_turn_falls_back_to_env_key_when_header_missing(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-fallback-key-0000")
    client.post("/api/call/start")
    r = client.post("/api/call/turn", json={"call_id": "abc", "text": "배송비"})
    assert r.status_code == 200


def test_turn_header_key_is_forwarded_to_pipeline(modumall_dir):
    pipeline = FakePipeline()
    c = TestClient(create_app(pipeline, load_domain(modumall_dir)))
    c.post("/api/call/start")
    c.post("/api/call/turn", json={"call_id": "abc", "text": "배송비"}, headers=HEADERS_WITH_KEY)
    assert pipeline.turn_api_keys == [HEADERS_WITH_KEY["X-OpenAI-Key"]]


def test_key_is_never_echoed_back_in_error_response(modumall_dir):
    """전역 예외 핸들러가 예외 메시지를 그대로 돌려주므로, 키가 섞여 있으면 가려야 한다."""
    c = TestClient(create_app(KeyLeakPipeline(), load_domain(modumall_dir)), raise_server_exceptions=False)
    c.post("/api/call/start")
    r = c.post("/api/call/turn", json={"call_id": "abc", "text": "배송비"}, headers=HEADERS_WITH_KEY)
    assert r.status_code == 500
    body = r.text
    assert HEADERS_WITH_KEY["X-OpenAI-Key"] not in body
    assert "[REDACTED]" in body


class MaskedKeyLeakPipeline(FakePipeline):
    """OpenAI 인증 오류는 키 원문을 그대로 주지 않고 "sk-fake-*****zzzz" 처럼 가운데를
    별표로 가려서 일부만 돌려주기도 한다. 이 조각도 새 나가면 안 된다."""
    def turn(self, call_id, text, api_key=None):
        raise RuntimeError("Incorrect API key provided: sk-fake-*****************zzzz.")


def test_masked_key_fragment_is_also_redacted(modumall_dir):
    c = TestClient(create_app(MaskedKeyLeakPipeline(), load_domain(modumall_dir)), raise_server_exceptions=False)
    c.post("/api/call/start")
    r = c.post("/api/call/turn", json={"call_id": "abc", "text": "배송비"}, headers=HEADERS_WITH_KEY)
    assert r.status_code == 500
    body = r.text
    assert "sk-fake" not in body and "zzzz" not in body
    assert "[REDACTED]" in body


class HeaderKeyLeakPipeline(FakePipeline):
    """예외 메시지에 이번 요청의 키 원문을 그대로 실어 보낸다. 정규식(_KEY_PATTERN)은
    "sk-" 로 시작하지 않는 키나 중간에 공백이 섞인 키를 놓친다(실측 확인) — 이 요청에서
    실제로 쓰인 키 문자열 자체를 literal 치환하는 이중 방어(server.llmkey.current_request_key)
    가 있어야 걸러진다."""
    def turn(self, call_id, text, api_key=None):
        raise RuntimeError(f"upstream rejected key: {api_key}")


def test_non_sk_prefixed_key_is_redacted_via_literal_replace(modumall_dir):
    c = TestClient(create_app(HeaderKeyLeakPipeline(), load_domain(modumall_dir)), raise_server_exceptions=False)
    c.post("/api/call/start")
    odd_key = "NOSKPREFIX1234567890abcdef"
    r = c.post("/api/call/turn", json={"call_id": "abc", "text": "배송비"},
               headers={"X-OpenAI-Key": odd_key})
    assert r.status_code == 500
    assert odd_key not in r.text
    assert "[REDACTED]" in r.text


def test_key_with_embedded_space_is_redacted_via_literal_replace(modumall_dir):
    c = TestClient(create_app(HeaderKeyLeakPipeline(), load_domain(modumall_dir)), raise_server_exceptions=False)
    c.post("/api/call/start")
    spaced_key = "sk-abc def-1234567890"
    r = c.post("/api/call/turn", json={"call_id": "abc", "text": "배송비"},
               headers={"X-OpenAI-Key": spaced_key})
    assert r.status_code == 500
    assert spaced_key not in r.text
    assert "[REDACTED]" in r.text


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "<html" in r.text.lower()


def test_pipeline_error_is_500_with_message(modumall_dir):
    broken_client = TestClient(create_app(BrokenPipeline(), load_domain(modumall_dir)), raise_server_exceptions=False)
    broken_client.post("/api/call/start")
    r = broken_client.post("/api/call/turn", json={"call_id": "abc", "text": "배송비"}, headers=HEADERS_WITH_KEY)
    assert r.status_code == 500 and "LLM 호출 실패" in r.json()["detail"]


def test_tts_501_when_not_configured(client):
    r = client.post("/api/tts", json={"text": "안녕하세요"})
    assert r.status_code == 501
    assert client.get("/api/domain").json()["tts_available"] is False


def test_start_call_accepts_phone(client):
    r = client.post("/api/call/start", json={"phone": "010-1111-2222"}).json()
    assert r["call_id"] == "abc" and "customer" in r
    assert client.get("/api/domain").json()["sample_customers"] == []   # FakePipeline 는 샘플 없음


def test_end_call(client):
    assert client.post("/api/call/end", json={"call_id": "abc"}).json() == {"ok": True}


def test_tts_returns_audio_when_configured(modumall_dir):
    app = create_app(FakePipeline(), load_domain(modumall_dir),
                     tts=lambda text, api_key=None: b"ID3fake-mp3:" + text.encode())
    c = TestClient(app)
    assert c.get("/api/domain").json()["tts_available"] is True
    r = c.post("/api/tts", json={"text": "오 다시 일 공 공 일"}, headers=HEADERS_WITH_KEY)
    assert r.status_code == 200 and r.headers["content-type"].startswith("audio/mpeg")
    assert r.content.startswith(b"ID3fake-mp3:")
    assert c.post("/api/tts", json={"text": "  "}, headers=HEADERS_WITH_KEY).status_code == 422


def test_tts_without_key_and_without_env_fallback_is_401(modumall_dir, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = create_app(FakePipeline(), load_domain(modumall_dir),
                     tts=lambda text, api_key=None: b"ID3fake-mp3:" + text.encode())
    c = TestClient(app)
    r = c.post("/api/tts", json={"text": "안녕하세요"})
    assert r.status_code == 401


def test_tts_key_is_forwarded(modumall_dir):
    seen = []
    app = create_app(FakePipeline(), load_domain(modumall_dir),
                     tts=lambda text, api_key=None: seen.append(api_key) or b"ID3fake-mp3:x")
    c = TestClient(app)
    c.post("/api/tts", json={"text": "안녕하세요"}, headers=HEADERS_WITH_KEY)
    assert seen == [HEADERS_WITH_KEY["X-OpenAI-Key"]]


class ProfilePipeline(FakePipeline):
    def start_call(self, phone=None):
        self.calls.add("abc")
        cust = {"customer_id": "C-0001", "name": "홍길동", "phone": phone, "recent_orders": []} if phone else None
        return "abc", "안녕하세요", cust

    def customer_profile(self, cid):
        return {"customer_id": cid, "name": "홍길동", "phone": "010-1111-2222", "address": "서울시 어딘가",
                "address_region": "수도권", "orders": [], "in_progress_count": 0}


class _FakeCheckLLM:
    def __init__(self, ok):
        self.ok = ok

    def invoke(self, messages):
        if not self.ok:
            raise RuntimeError("Incorrect API key provided: sk-bad-0000000000000000")
        return "pong"


def test_key_check_without_key_is_401(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    r = client.post("/api/key/check")
    assert r.status_code == 401


def test_key_check_valid_key(client, monkeypatch):
    monkeypatch.setattr("server.llmkey.get_chat_model", lambda model, key, cache=True: _FakeCheckLLM(True))
    r = client.post("/api/key/check", headers=HEADERS_WITH_KEY).json()
    assert r == {"ok": True, "message": "사용할 수 있는 키입니다"}


def test_key_check_invalid_key_does_not_leak_error(client, monkeypatch):
    monkeypatch.setattr("server.llmkey.get_chat_model", lambda model, key, cache=True: _FakeCheckLLM(False))
    r = client.post("/api/key/check", headers=HEADERS_WITH_KEY)
    body = r.json()
    assert body["ok"] is False and body["message"] == "키가 올바르지 않습니다"
    assert "sk-bad" not in r.text and "Incorrect API key" not in r.text


def test_start_call_returns_admin_profile_separately(modumall_dir):
    c = TestClient(create_app(ProfilePipeline(), load_domain(modumall_dir)))
    r = c.post("/api/call/start", json={"phone": "010-1111-2222"}).json()
    assert r["customer"]["name"] == "홍길동" and "address" not in r["customer"]   # 프롬프트용 dict 에는 주소가 없다
    assert r["profile"]["address"] == "서울시 어딘가" and r["profile"]["customer_id"] == "C-0001"
    r = c.post("/api/call/start", json={}).json()
    assert r["customer"] is None and r["profile"] is None
