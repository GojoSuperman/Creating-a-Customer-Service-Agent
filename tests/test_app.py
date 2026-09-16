import pytest
from fastapi.testclient import TestClient
from server.app import create_app
from server.domain import load_domain
from server.pipeline import TurnResult


class FakePipeline:
    def __init__(self):
        self.calls = set()
        self.ended = []

    def start_call(self, phone=None):
        self.calls.add("abc")
        return "abc", "안녕하세요", None

    def end_call(self, call_id):
        self.ended.append(call_id)

    def turn(self, call_id, text):
        if call_id not in self.calls:
            raise KeyError(call_id)
        return TurnResult(answer=f"응답:{text}", route="SHIPPING", confidence=0.9, action="ANSWER",
                          tools=[], guardrail={"ok": True, "violations": []}, elapsed_ms=5, end_call=False,
                          attempts=1)


class BrokenPipeline(FakePipeline):
    def turn(self, call_id, text):
        raise RuntimeError("LLM 호출 실패")


@pytest.fixture
def client(modumall_dir):
    return TestClient(create_app(FakePipeline(), load_domain(modumall_dir)))


def test_domain_endpoint(client):
    r = client.get("/api/domain")
    assert r.status_code == 200 and r.json()["name"] == "모두몰" and "greeting" in r.json()


def test_start_and_turn(client):
    s = client.post("/api/call/start").json()
    assert s["call_id"] == "abc" and s["greeting"]
    t = client.post("/api/call/turn", json={"call_id": "abc", "text": "배송비"}).json()
    assert t["answer"] == "응답:배송비" and t["action"] == "ANSWER" and t["end_call"] is False


def test_unknown_call_is_404(client):
    r = client.post("/api/call/turn", json={"call_id": "zzz", "text": "x"})
    assert r.status_code == 404


def test_empty_text_is_422(client):
    r = client.post("/api/call/turn", json={"call_id": "abc", "text": "  "})
    assert r.status_code == 422


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "<html" in r.text.lower()


def test_pipeline_error_is_500_with_message(modumall_dir):
    broken_client = TestClient(create_app(BrokenPipeline(), load_domain(modumall_dir)), raise_server_exceptions=False)
    broken_client.post("/api/call/start")
    r = broken_client.post("/api/call/turn", json={"call_id": "abc", "text": "배송비"})
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
    app = create_app(FakePipeline(), load_domain(modumall_dir), tts=lambda text: b"ID3fake-mp3:" + text.encode())
    c = TestClient(app)
    assert c.get("/api/domain").json()["tts_available"] is True
    r = c.post("/api/tts", json={"text": "오 다시 일 공 공 일"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("audio/mpeg")
    assert r.content.startswith(b"ID3fake-mp3:")
    assert c.post("/api/tts", json={"text": "  "}).status_code == 422


class ProfilePipeline(FakePipeline):
    def start_call(self, phone=None):
        self.calls.add("abc")
        cust = {"customer_id": "C-0001", "name": "홍길동", "phone": phone, "recent_orders": []} if phone else None
        return "abc", "안녕하세요", cust

    def customer_profile(self, cid):
        return {"customer_id": cid, "name": "홍길동", "phone": "010-1111-2222", "address": "서울시 어딘가",
                "address_region": "수도권", "orders": [], "in_progress_count": 0}


def test_start_call_returns_admin_profile_separately(modumall_dir):
    c = TestClient(create_app(ProfilePipeline(), load_domain(modumall_dir)))
    r = c.post("/api/call/start", json={"phone": "010-1111-2222"}).json()
    assert r["customer"]["name"] == "홍길동" and "address" not in r["customer"]   # 프롬프트용 dict 에는 주소가 없다
    assert r["profile"]["address"] == "서울시 어딘가" and r["profile"]["customer_id"] == "C-0001"
    r = c.post("/api/call/start", json={}).json()
    assert r["customer"] is None and r["profile"] is None
